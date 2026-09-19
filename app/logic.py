"""EDFlow bottleneck logic — utilization percentages, a green/yellow/red
status, per-patient wait tracking, and allocation recommendations.

The thresholds and formulas below are simple, tunable rules of thumb for
this demo, not real clinical or hospital-operations standards.
"""

import math
from datetime import datetime

from app import forecast
from app.config import (
    FLOAT_ROOM_BED_COUNT,
    NURSE_MAX_PATIENTS,
    NURSE_TRIAGE_WEIGHT,
    PHYSICIAN_MAX_PATIENTS,
    PHYSICIAN_TRIAGE_WEIGHT,
    PROACTIVE_BREACH_WINDOW_MINUTES,
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


def _wait_minutes(arrival_time, now):
    """A patient's actual elapsed wait, in simulated minutes, from their
    real arrival timestamp to `now` — the one wait definition shared by
    the scorecard, breach summary, tier table, and patient list, so they
    always reconcile."""
    elapsed_seconds = (now - datetime.fromisoformat(arrival_time)).total_seconds()
    return round(max(elapsed_seconds, 0) * SIM_MINUTES_PER_REAL_SECOND, 1)


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
    # A room counts as "in use" while any of its beds is occupied OR still
    # mid-turnover — a bed being cleaned isn't free for the next patient
    # yet, so the room isn't really available either.
    rooms_in_use = conn.execute(
        "SELECT COUNT(DISTINCT room_id) AS n FROM beds WHERE status IN ('occupied', 'cleaning')"
    ).fetchone()["n"]

    waiting_rows = conn.execute(
        "SELECT id, arrival_time FROM patients WHERE status = 'waiting' ORDER BY acuity ASC, arrival_time ASC"
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

    # Displayed average wait: the mean of each currently-waiting patient's
    # own actual elapsed wait on the simulated clock — the same per-patient
    # figure get_breach_summary() and get_patients_list() already compute,
    # so the scorecard, tier table, charts, and situation report all agree
    # with each other instead of showing two different definitions of
    # "wait."
    now = datetime.now()
    if patients_waiting:
        avg_wait_minutes = round(
            sum(_wait_minutes(row["arrival_time"], now) for row in waiting_rows) / patients_waiting, 1
        )
    else:
        avg_wait_minutes = 0.0

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
        wait_minutes = _wait_minutes(row["arrival_time"], now)
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


def _breach_projection_phrase(projected_breach_minutes):
    """'in ~6 min' or, once beds are already at zero, 'imminently' — ~0 min
    reads oddly since it's not really a future event anymore."""
    if projected_breach_minutes <= 0.5:
        return "imminently"
    return f"in ~{projected_breach_minutes:g} min"


def _units_needed(pct, capacity_total, per_unit_capacity, target_pct=YELLOW_THRESHOLD):
    """How many more `per_unit_capacity`-sized units (one nurse, one
    physician) would bring utilization back down to target_pct — 0 if
    already at or under target. A simple ratio-based sizing, not a real
    staffing model."""
    if capacity_total <= 0 or pct <= target_pct or per_unit_capacity <= 0:
        return 0
    deficit_workload = (pct - target_pct) / 100 * capacity_total
    return math.ceil(deficit_workload / per_unit_capacity)


def get_recommendations(utilization, breach_summary, float_pool, overflow_pool):
    """Recommend the next capped allocation action, as an escalation ladder:

        1. The ED's own resources/turnover
        2. The hospital float pool (nurses, physicians, rooms)
        3. Overflow capacity (extra physical beds within the ED)
        4. Relocation to nearby facilities

    Quantities are need-based: sized from current load against realistic
    per-nurse/per-physician/per-room ratios (see _units_needed above and
    FLOAT_ROOM_BED_COUNT), then capped at what's actually available — never
    a number bigger than the reserve it's drawn from. This is a rules-based
    heuristic for a demo, not a real staffing or capacity-planning model.

    Escalation is also PROACTIVE: app/forecast.py projects how many minutes
    remain before beds run out (from how fast they've been filling), and
    this ladder starts recommending once that projected breach is within
    PROACTIVE_BREACH_WINDOW_MINUTES — not only after Tier 1-2 patients have
    actually started missing their target wait. Before this fix, escalation
    was gated entirely on tier12_breaches > 0, but tier-priority placement
    means Tier 1-2 patients are almost always seated first and rarely
    breach even while every other resource is completely saturated — so the
    ladder would sit on "Monitor" through an entire surge no matter how
    many Tier 3-5 patients piled up waiting.
    """
    beds = utilization["beds"]
    rooms = utilization["rooms"]
    nurses = utilization["nurses"]
    physicians = utilization["physicians"]
    waiting = utilization["patients_waiting"]
    avg_wait = utilization["avg_wait_minutes"]
    tier12_breaches = breach_summary["tier1_2_breaches"]

    forecast.note(beds["available"])
    projected_breach_minutes = None
    if tier12_breaches == 0:
        projected_breach_minutes = forecast.estimate_minutes_to_breach(beds["available"])

    breach_imminent = tier12_breaches > 0 or (
        projected_breach_minutes is not None and projected_breach_minutes <= PROACTIVE_BREACH_WINDOW_MINUTES
    )

    no_strain = beds["pct"] < YELLOW_THRESHOLD and nurses["pct"] < YELLOW_THRESHOLD and physicians["pct"] < YELLOW_THRESHOLD

    # Rung 0: genuinely nothing to do.
    if not breach_imminent and no_strain:
        return [
            {
                "rung": 0,
                "action": "No action needed",
                "reason": "All resources have headroom and no Tier 1-2 patients are past target.",
                "estimated_wait_reduction_minutes": 0,
                "quantities": {},
                "projected_breach_minutes": None,
            }
        ]

    # Rung 1: the ED's own resources/turnover. Strain, but no breach has
    # happened and none is imminent — normal discharge throughput should
    # keep pace without reaching outside the ED.
    if not breach_imminent:
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
                "projected_breach_minutes": None,
            }
        ]

    proactive = tier12_breaches == 0  # imminent by projection, not an actual breach yet
    ladder = []

    # Rung 2: hospital float pool (nurses, physicians, rooms), sized from
    # actual current load against realistic per-unit ratios. While
    # proactively triggered (a breach is projected but hasn't happened),
    # anything trending past the yellow threshold gets at least 1 unit
    # recommended even if the ratio-based deficit still rounds to 0 — the
    # point is to act before the breach, not size to an already-overdue one.
    nurses_needed_flag = nurses["pct"] >= YELLOW_THRESHOLD
    physicians_needed_flag = physicians["pct"] >= YELLOW_THRESHOLD
    rooms_needed_flag = bool(rooms["total"]) and rooms["in_use"] >= rooms["total"]

    bed_shortfall = max(0, waiting - beds["available"])

    nurses_needed = _units_needed(nurses["pct"], nurses["capacity_total"], NURSE_MAX_PATIENTS)
    physicians_needed = _units_needed(physicians["pct"], physicians["capacity_total"], PHYSICIAN_MAX_PATIENTS)
    rooms_needed = math.ceil(bed_shortfall / FLOAT_ROOM_BED_COUNT) if rooms_needed_flag else 0

    if proactive:
        if nurses_needed_flag:
            nurses_needed = max(nurses_needed, 1)
        if physicians_needed_flag:
            physicians_needed = max(physicians_needed, 1)
        if rooms_needed_flag:
            rooms_needed = max(rooms_needed, 1)

    nurses_pulled = min(nurses_needed, float_pool["nurses_available"]) if nurses_needed_flag else 0
    physicians_pulled = min(physicians_needed, float_pool["physicians_available"]) if physicians_needed_flag else 0
    rooms_pulled = min(rooms_needed, float_pool["rooms_available"]) if rooms_needed_flag else 0

    rung2_applicable = nurses_needed_flag or physicians_needed_flag or rooms_needed_flag
    rung2_fully_covered = True

    if rung2_applicable:
        rung2_fully_covered = (
            (not nurses_needed_flag or nurses_pulled >= nurses_needed)
            and (not physicians_needed_flag or physicians_pulled >= physicians_needed)
            and (not rooms_needed_flag or rooms_pulled >= rooms_needed)
        )

        parts = []
        if nurses_pulled:
            parts.append(f"{nurses_pulled} nurse{'s' if nurses_pulled != 1 else ''}")
        if physicians_pulled:
            parts.append(f"{physicians_pulled} physician{'s' if physicians_pulled != 1 else ''}")
        if rooms_pulled:
            parts.append(f"{rooms_pulled} room{'s' if rooms_pulled != 1 else ''}")

        if proactive:
            reason = (
                f"Projected Tier 1-2 breach {_breach_projection_phrase(projected_breach_minutes)} at the current "
                f"bed-fill rate; capacity is already strained (beds {beds['pct']}%, nurses {nurses['pct']}%, "
                f"physicians {physicians['pct']}%)."
            )
        else:
            reason = (
                f"{tier12_breaches} Tier 1-2 patient{'s' if tier12_breaches != 1 else ''} past target; "
                f"ED capacity is strained (beds {beds['pct']}%, nurses {nurses['pct']}%, "
                f"physicians {physicians['pct']}%)."
            )
        shortfalls = []
        if nurses_needed_flag and nurses_pulled < nurses_needed:
            shortfalls.append(f"only {float_pool['nurses_available']} nurse(s) left in the float pool")
        if physicians_needed_flag and physicians_pulled < physicians_needed:
            shortfalls.append(f"only {float_pool['physicians_available']} physician(s) left")
        if rooms_needed_flag and rooms_pulled < rooms_needed:
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
                )
                if not proactive
                else 0,
                "quantities": {"nurses": nurses_pulled, "physicians": physicians_pulled, "rooms": rooms_pulled},
                "projected_breach_minutes": projected_breach_minutes if proactive else None,
            }
        )

    # Rung 3: overflow capacity (extra physical beds within the ED), sized
    # directly from the current bed shortfall.
    beds_needed_flag = bed_shortfall > 0
    beds_opened = min(bed_shortfall, overflow_pool["overflow_beds_available"]) if beds_needed_flag else 0
    rung3_fully_covered = (not beds_needed_flag) or beds_opened >= bed_shortfall

    if beds_needed_flag:
        reason = (
            f"{waiting} patient{'s' if waiting != 1 else ''} waiting with only "
            f"{beds['available']} bed{'s' if beds['available'] != 1 else ''} open "
            f"({bed_shortfall} more bed{'s' if bed_shortfall != 1 else ''} needed to clear the backlog)."
        )
        if beds_opened < bed_shortfall:
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
                "estimated_wait_reduction_minutes": round(min(avg_wait, beds_opened * 8), 1) if not proactive else 0,
                "quantities": {"beds": beds_opened},
                "projected_breach_minutes": projected_breach_minutes if proactive else None,
            }
        )

    if not ladder:
        # Neither the float pool nor overflow beds are actually applicable
        # (nurses, physicians, rooms, and beds all still have headroom) —
        # surface why rather than silently returning nothing. This can
        # happen either proactively (a breach is projected from the bed-
        # fill rate but hasn't happened) or reactively (an actual Tier 1-2
        # breach exists — Tier 1's target is 0 minutes, so even a brief
        # wait counts — while every resource is still otherwise fine and
        # should resolve on its own).
        if proactive:
            reason = (
                f"Projected Tier 1-2 breach {_breach_projection_phrase(projected_breach_minutes)} at the "
                f"current bed-fill rate (beds {beds['pct']}%). Nothing to pull from the float pool or "
                f"overflow yet, but keep watching."
            )
        else:
            reason = (
                f"{tier12_breaches} Tier 1-2 patient{'s are' if tier12_breaches != 1 else ' is'} past target, "
                f"but beds, nurses, physicians, and rooms all still have headroom (beds {beds['pct']}%, nurses "
                f"{nurses['pct']}%, physicians {physicians['pct']}%) — this should resolve on its own as the "
                f"ED's own turnover catches up."
            )
        return [
            {
                "rung": 1,
                "action": "Monitor — bed occupancy is trending up" if proactive else "Monitor — ED's own turnover should keep pace",
                "reason": reason,
                "estimated_wait_reduction_minutes": 0,
                "quantities": {},
                "projected_breach_minutes": projected_breach_minutes if proactive else None,
            }
        ]

    if rung2_fully_covered and rung3_fully_covered:
        return ladder

    if proactive:
        # Don't recommend relocating patients over a projection that
        # hasn't happened yet — rung 4 is reserved for an actual, current
        # Tier 1-2 breach that the float pool and overflow couldn't cover.
        return ladder

    # Rung 4: relocate lower-tier patients to nearby facilities. Never
    # Tier 1 — the relocation engine itself enforces that; this is just the
    # recommendation surface for it.
    units_needed = max(tier12_breaches, 1)
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
            "projected_breach_minutes": None,
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
        if row["status"] == "waiting":
            reference_time = now
        elif row["bed_assigned_at"]:
            reference_time = datetime.fromisoformat(row["bed_assigned_at"])
        else:
            reference_time = datetime.fromisoformat(row["arrival_time"])

        wait_minutes = _wait_minutes(row["arrival_time"], reference_time)
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
