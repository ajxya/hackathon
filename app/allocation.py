"""EDFlow allocation engine — the shared logic behind every event.

This is what makes check-ins, ambulance arrivals, the surge simulator, and
the passage of time all affect the SAME underlying state: patients, beds,
nurses, and physicians. Two things happen here:

1. Placement: when a bed AND a nurse AND a physician are all free, the
   highest-priority waiting patient (lowest tier number, then longest
   waiting) gets placed into a bed.
2. Discharge: every DISCHARGE_TICK_SECONDS, a fraction of currently
   admitted patients (oldest-admitted first) are discharged, freeing their
   bed/nurse/physician back up — which can then immediately be given to
   the next waiting patient.

Both events and every GET /state call run the same advance_state()
function, so there's only one source of truth for how the ED evolves.
"""

from datetime import datetime, timedelta

from app.config import DISCHARGE_FRACTION, DISCHARGE_TICK_SECONDS
from app.lwbs import process_lwbs
from app.reset_registry import register_reset

# In-memory clock for the discharge tick. Resets on server restart and on
# every demo reset (see reset_discharge_clock()) — there's no need for this
# to survive a restart, since a fresh run naturally starts a fresh clock.
_next_discharge_tick = None


@register_reset
def reset_discharge_clock():
    global _next_discharge_tick
    _next_discharge_tick = None


def advance_state(conn):
    """Run one pass of the simulation: discharge anyone due, then try to
    place waiting patients into whatever capacity that frees up.

    BEGIN IMMEDIATE grabs SQLite's write lock up front, so this whole pass
    runs as one atomic unit even when a surge tick and a dashboard poll land
    at the same instant — otherwise two concurrent requests could both see
    the same bed as "available" and double-book it.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        _process_discharges(conn)
        process_lwbs(conn)  # remove anyone who's given up waiting, before trying to place the rest
        _place_waiting_patients(conn)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _process_discharges(conn):
    global _next_discharge_tick
    now = datetime.now()

    if _next_discharge_tick is None:
        # First call after a (re)start: arm the clock, but don't discharge
        # anyone yet — nothing has had a chance to wait a full tick.
        _next_discharge_tick = now + timedelta(seconds=DISCHARGE_TICK_SECONDS)
        return

    ticks_run = 0
    # Catch up on any ticks that elapsed while nobody was polling, capped so
    # a long-idle server doesn't loop forever processing ancient ticks.
    while now >= _next_discharge_tick and ticks_run < 20:
        _run_one_discharge_batch(conn)
        _next_discharge_tick += timedelta(seconds=DISCHARGE_TICK_SECONDS)
        ticks_run += 1


def _run_one_discharge_batch(conn):
    occupied = conn.execute("SELECT COUNT(*) AS n FROM patients WHERE status = 'in_bed'").fetchone()["n"]
    if occupied == 0:
        return

    discharge_count = max(1, round(occupied * DISCHARGE_FRACTION))
    due = conn.execute(
        """
        SELECT id, bed_id, nurse_id, physician_id FROM patients
        WHERE status = 'in_bed'
        ORDER BY bed_assigned_at ASC
        LIMIT ?
        """,
        (discharge_count,),
    ).fetchall()

    for patient in due:
        conn.execute("UPDATE patients SET status = 'discharged' WHERE id = ?", (patient["id"],))
        if patient["bed_id"] is not None:
            conn.execute("UPDATE beds SET status = 'available' WHERE id = ?", (patient["bed_id"],))
        _release_staff(conn, "nurses", patient["nurse_id"])
        _release_staff(conn, "physicians", patient["physician_id"])


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

    now = datetime.now().isoformat(timespec="seconds")

    conn.execute(
        """
        UPDATE patients
        SET status = 'in_bed', bed_id = ?, nurse_id = ?, physician_id = ?, bed_assigned_at = ?
        WHERE id = ?
        """,
        (bed["id"], nurse["id"], physician["id"], now, patient_id),
    )
    conn.execute("UPDATE beds SET status = 'occupied' WHERE id = ?", (bed["id"],))
    _increment_staff(conn, "nurses", nurse)
    _increment_staff(conn, "physicians", physician)
    return True


def _pick_staff_with_capacity(conn, table):
    return conn.execute(
        f"SELECT id, current_patients, max_patients FROM {table} "
        f"WHERE current_patients < max_patients ORDER BY current_patients ASC LIMIT 1"
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
