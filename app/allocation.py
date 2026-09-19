"""EDFlow allocation engine — the shared logic behind every event.

This is what makes check-ins, ambulance arrivals, the surge simulator, and
the passage of time all affect the SAME underlying state: patients, beds,
nurses, and physicians. Three things happen here, in order:

1. Bed turnover: any bed whose cleaning delay has elapsed becomes
   available again.
2. Discharge: every admitted patient whose OWN length of stay has
   elapsed is discharged, freeing their bed/nurse/physician (the bed then
   starts its own cleaning delay before it can be reused). Driven
   entirely by each patient's stored discharge_due_at — there's no fixed
   shared schedule — so the departure rate rises and falls naturally with
   how many patients are actually in beds, rather than being capped at a
   fixed batch size.
3. Placement: when a bed AND a nurse AND a physician are all free, the
   highest-priority waiting patient (lowest tier number, then longest
   waiting) gets placed into a bed and given their own length of stay,
   drawn from TIER_LOS_MINUTES.

Both events and every GET /state call run the same advance_state()
function, so there's only one source of truth for how the ED evolves. All
of this state (discharge_due_at, cleaning_until) lives in the database
itself, wiped and reseeded on every demo reset — so there's no separate
in-memory clock to cancel or reset here.
"""

import random
from datetime import datetime, timedelta

from app.config import (
    BED_TURNOVER_MINUTES,
    FLOAT_RETURN_GRACE_SECONDS,
    FLOAT_RETURN_THRESHOLD_PCT,
    FLOAT_STAFF_ARRIVAL_DELAY_SECONDS,
    SIM_MINUTES_PER_REAL_SECOND,
    TIER_LOS_MINUTES,
)
from app import baseline_simulation
from app.lwbs import process_lwbs
from app.reserve import return_float_pool, return_overflow_pool


def advance_state(conn):
    """Run one pass of the simulation: free up any beds done cleaning,
    discharge anyone whose stay is up, then try to place waiting patients
    into whatever capacity that frees up.

    BEGIN IMMEDIATE grabs SQLite's write lock up front, so this whole pass
    runs as one atomic unit even when a surge tick and a dashboard poll land
    at the same instant — otherwise two concurrent requests could both see
    the same bed as "available" and double-book it.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        _release_finished_cleanings(conn)
        _process_discharges(conn)
        process_lwbs(conn)  # remove anyone who's given up waiting, before trying to place the rest
        _place_waiting_patients(conn)
        _return_idle_float_resources(conn)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

    # The shadow baseline simulation (Step 4) is separate in-memory state,
    # not part of this SQL transaction — advance it on every real-ED tick/
    # poll too, so both always progress on exactly the same ticks.
    baseline_simulation.advance()


def sim_minutes_to_real_seconds(minutes):
    return minutes / SIM_MINUTES_PER_REAL_SECOND


def draw_los_seconds(tier):
    """A random length of stay (in real seconds) for a newly-admitted
    patient of this tier, drawn from TIER_LOS_MINUTES and converted from
    simulated minutes onto the real clock."""
    low, high = TIER_LOS_MINUTES.get(tier, TIER_LOS_MINUTES[5])
    return sim_minutes_to_real_seconds(random.uniform(low, high))


def _process_discharges(conn):
    now = datetime.now().isoformat(timespec="seconds")
    due = conn.execute(
        "SELECT id, bed_id, nurse_id, physician_id FROM patients "
        "WHERE status = 'in_bed' AND discharge_due_at <= ?",
        (now,),
    ).fetchall()

    for patient in due:
        # Clear the bed/staff references immediately (not just the status)
        # so a float room/bed or float staff member can later be deleted
        # outright when returned to the pool, without leaving a dangling
        # foreign key on this now-discharged patient's old row.
        conn.execute(
            """
            UPDATE patients
            SET status = 'discharged', discharge_due_at = NULL, bed_id = NULL, nurse_id = NULL, physician_id = NULL
            WHERE id = ?
            """,
            (patient["id"],),
        )
        if patient["bed_id"] is not None:
            _start_bed_cleaning(conn, patient["bed_id"])
        _release_staff(conn, "nurses", patient["nurse_id"])
        _release_staff(conn, "physicians", patient["physician_id"])


def _start_bed_cleaning(conn, bed_id):
    cleaning_until = (
        datetime.now() + timedelta(seconds=sim_minutes_to_real_seconds(BED_TURNOVER_MINUTES))
    ).isoformat(timespec="seconds")
    conn.execute("UPDATE beds SET status = 'cleaning', cleaning_until = ? WHERE id = ?", (cleaning_until, bed_id))


def _release_finished_cleanings(conn):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE beds SET status = 'available', cleaning_until = NULL "
        "WHERE status = 'cleaning' AND cleaning_until <= ?",
        (now,),
    )


def _place_waiting_patients(conn):
    """Try to place every waiting patient, highest priority (lowest tier
    number) and longest-waiting first."""
    waiting = conn.execute(
        "SELECT id FROM patients WHERE status = 'waiting' ORDER BY acuity ASC, arrival_time ASC"
    ).fetchall()

    for row in waiting:
        _try_place_patient(conn, row["id"])


def _try_place_patient(conn, patient_id):
    bed = conn.execute("SELECT id FROM beds WHERE status = 'available' ORDER BY id LIMIT 1").fetchone()
    if not bed:
        return False

    nurse = _pick_staff_with_capacity(conn, "nurses")
    if not nurse:
        return False

    physician = _pick_staff_with_capacity(conn, "physicians")
    if not physician:
        return False

    patient = conn.execute("SELECT acuity, los_seconds FROM patients WHERE id = ?", (patient_id,)).fetchone()
    now_dt = datetime.now()
    now = now_dt.isoformat(timespec="seconds")
    # Use the length of stay drawn once at arrival (app/patients.py,
    # app/seed.py) — not a fresh draw here — so the real patient and their
    # mirrored copy in the shadow baseline simulation match exactly.
    los_seconds = patient["los_seconds"] if patient["los_seconds"] is not None else draw_los_seconds(patient["acuity"])
    discharge_due_at = (now_dt + timedelta(seconds=los_seconds)).isoformat(timespec="seconds")

    conn.execute(
        """
        UPDATE patients
        SET status = 'in_bed', bed_id = ?, nurse_id = ?, physician_id = ?,
            bed_assigned_at = ?, discharge_due_at = ?
        WHERE id = ?
        """,
        (bed["id"], nurse["id"], physician["id"], now, discharge_due_at, patient_id),
    )
    conn.execute("UPDATE beds SET status = 'occupied' WHERE id = ?", (bed["id"],))
    _increment_staff(conn, "nurses", nurse)
    _increment_staff(conn, "physicians", physician)
    return True


def _return_idle_float_resources(conn):
    """Once bed occupancy is comfortably low, give back any float nurses/
    physicians/rooms and overflow beds that were applied but are sitting
    idle — a surge that's clearly passed shouldn't leave borrowed capacity
    permanently folded into the ED's own roster."""
    beds_row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) AS occupied FROM beds"
    ).fetchone()
    total = beds_row["total"] or 0
    occupied = beds_row["occupied"] or 0
    if total == 0 or (occupied / total * 100) >= FLOAT_RETURN_THRESHOLD_PCT:
        return

    _return_idle_float_staff(conn, "nurses")
    _return_idle_float_staff(conn, "physicians")
    _return_idle_float_rooms(conn)
    _return_idle_overflow_beds(conn)


def _grace_cutoff():
    """Timestamp before which a float resource is old enough to be
    eligible for automatic return — see FLOAT_RETURN_GRACE_SECONDS."""
    return (datetime.now() - timedelta(seconds=FLOAT_RETURN_GRACE_SECONDS)).isoformat(timespec="seconds")


def _return_idle_float_staff(conn, table):
    idle = conn.execute(
        f"SELECT id FROM {table} WHERE source = 'float' AND current_patients = 0 "
        f"AND added_at IS NOT NULL AND added_at <= ?",
        (_grace_cutoff(),),
    ).fetchall()
    if not idle:
        return
    conn.executemany(f"DELETE FROM {table} WHERE id = ?", [(row["id"],) for row in idle])
    return_float_pool(**{table: len(idle)})


def _return_idle_float_rooms(conn):
    cutoff = _grace_cutoff()
    rooms = conn.execute("SELECT id FROM rooms WHERE room_type = 'Float'").fetchall()
    returned = 0
    for room in rooms:
        beds = conn.execute("SELECT status, created_at FROM beds WHERE room_id = ?", (room["id"],)).fetchall()
        if beds and all(b["status"] == "available" and b["created_at"] and b["created_at"] <= cutoff for b in beds):
            conn.execute("DELETE FROM beds WHERE room_id = ?", (room["id"],))
            conn.execute("DELETE FROM rooms WHERE id = ?", (room["id"],))
            returned += 1
    if returned:
        return_float_pool(rooms=returned)


def _return_idle_overflow_beds(conn):
    idle = conn.execute(
        "SELECT b.id AS bed_id, b.room_id FROM beds b "
        "JOIN rooms r ON b.room_id = r.id "
        "WHERE r.room_type = 'Overflow' AND b.status = 'available' "
        "AND b.created_at IS NOT NULL AND b.created_at <= ?",
        (_grace_cutoff(),),
    ).fetchall()
    if not idle:
        return

    for row in idle:
        conn.execute("DELETE FROM beds WHERE id = ?", (row["bed_id"],))

    return_overflow_pool(beds=len(idle))

    # Clean up the shared "Overflow Bay" room once every overflow bed in
    # it has been returned, so an empty room doesn't linger.
    room_ids = {row["room_id"] for row in idle}
    for room_id in room_ids:
        remaining = conn.execute("SELECT COUNT(*) AS c FROM beds WHERE room_id = ?", (room_id,)).fetchone()["c"]
        if remaining == 0:
            conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))


def _pick_staff_with_capacity(conn, table):
    # A float nurse/physician is pulled into the roster (and counted in
    # capacity totals) the instant Apply is clicked, but can't actually
    # take a patient until FLOAT_STAFF_ARRIVAL_DELAY_SECONDS has passed —
    # they still have to physically arrive.
    arrival_cutoff = (datetime.now() - timedelta(seconds=FLOAT_STAFF_ARRIVAL_DELAY_SECONDS)).isoformat(
        timespec="seconds"
    )
    return conn.execute(
        f"SELECT id, current_patients, max_patients FROM {table} "
        f"WHERE current_patients < max_patients "
        f"AND (source != 'float' OR added_at <= ?) "
        f"ORDER BY current_patients ASC LIMIT 1",
        (arrival_cutoff,),
    ).fetchone()


def _increment_staff(conn, table, staff_row):
    new_count = staff_row["current_patients"] + 1
    new_status = "busy" if new_count >= staff_row["max_patients"] else "available"
    conn.execute(f"UPDATE {table} SET current_patients = ?, status = ? WHERE id = ?", (new_count, new_status, staff_row["id"]))


def _release_staff(conn, table, staff_id):
    if staff_id is None:
        return
    row = conn.execute(f"SELECT current_patients, max_patients FROM {table} WHERE id = ?", (staff_id,)).fetchone()
    if not row:
        return
    new_count = max(row["current_patients"] - 1, 0)
    new_status = "busy" if new_count >= row["max_patients"] else "available"
    conn.execute(f"UPDATE {table} SET current_patients = ?, status = ? WHERE id = ?", (new_count, new_status, staff_id))
