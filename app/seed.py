"""EDFlow seed data — fills the database with a synthetic starting scenario.

Everything here is fake: made-up names, made-up arrival times. No real
patient information is used anywhere in this project.
"""

import random
from datetime import datetime, timedelta

from app import baseline_simulation
from app.allocation import draw_los_seconds
from app.config import NURSE_COUNT, NURSE_MAX_PATIENTS, PHYSICIAN_COUNT, PHYSICIAN_MAX_PATIENTS, RANDOM_SEED
from app.database import get_connection
from app.injuries import random_injury
from app.reset_registry import reset_all

ROOMS = [
    ("Room 1", "Trauma"),
    ("Room 2", "Trauma"),
    ("Room 3", "Trauma"),
    ("Room 4", "Exam"),
    ("Room 5", "Exam"),
    ("Room 6", "Exam"),
    ("Room 7", "Exam"),
    ("Room 8", "Triage"),
    ("Room 9", "Triage"),
    ("Room 10", "Triage"),
]

# 3 beds per room x 10 rooms = 30 beds.
BEDS_PER_ROOM = ("A", "B", "C")

# Name pools — only the first NURSE_COUNT/PHYSICIAN_COUNT (app/config.py)
# are actually used, so the pools stay larger than needed to leave room to
# raise those counts later without adding more names.
NURSE_NAMES = [
    "Nurse Alvarez", "Nurse Kim", "Nurse Patel", "Nurse Johnson", "Nurse Nguyen", "Nurse O'Brien",
    "Nurse Torres", "Nurse Whitfield", "Nurse Baptiste", "Nurse Sato", "Nurse Ferreira", "Nurse Okonkwo",
    "Nurse Delgado", "Nurse Hassan", "Nurse Novak", "Nurse Iwu", "Nurse Larsen", "Nurse Mbeki",
    "Nurse Grant", "Nurse Petrov", "Nurse Salas", "Nurse Wren", "Nurse Achebe", "Nurse Doyle",
    "Nurse Fontaine", "Nurse Amaral", "Nurse Csaki", "Nurse Nabors", "Nurse Osei", "Nurse Vance",
    "Nurse Ibarra", "Nurse Kessler", "Nurse Duarte", "Nurse Lund",
]
PHYSICIAN_NAMES = [
    "Dr. Chen", "Dr. Okafor", "Dr. Rossi", "Dr. Yamamoto", "Dr. Osei", "Dr. Kowalczyk",
    "Dr. Haddad", "Dr. Lindgren", "Dr. Adeyemi", "Dr. Marsh", "Dr. Bianchi", "Dr. Kimura",
    "Dr. Novak", "Dr. Farah", "Dr. Whitaker", "Dr. Solis", "Dr. Petrova", "Dr. Odom",
    "Dr. Salinas", "Dr. Berg", "Dr. Achterberg", "Dr. Nwosu",
]

# (name, source, tier 1-5, seconds_ago arrived, should_be_in_bed)
#
# These are real SECONDS ago, not minutes — under the compressed sim clock
# (SIM_MINUTES_PER_REAL_SECOND in app/config.py), 1 real second already
# counts as 1 simulated minute, so a handful of seconds is plenty to seed a
# believable starting scenario without every patient looking absurdly overdue.
PATIENTS = [
    ("Patient A. Carter", "walk-in", 3, 25, True),
    ("Patient B. Diaz", "ambulance", 2, 8, True),
    ("Patient C. Evans", "walk-in", 4, 15, True),
    ("Patient D. Foster", "ambulance", 1, 3, True),
    ("Patient E. Grant", "walk-in", 3, 12, False),
    ("Patient F. Hughes", "walk-in", 4, 5, False),
]


def seed_if_empty():
    """Only seed if the database is empty, so re-running the app doesn't duplicate data."""
    conn = get_connection()
    already_seeded = conn.execute("SELECT COUNT(*) AS c FROM rooms").fetchone()["c"] > 0
    if already_seeded:
        conn.close()
        return

    seed(conn)
    conn.close()


def seed(conn):
    """Wipe and insert a fresh synthetic starting scenario. Used by seed_if_empty and /demo/reset.

    reset_all() calls every registered in-memory reset callback (the
    discharge clock, the float pool, and anything future modules register)
    — so adding a new stateful module later doesn't require touching this
    function.

    The wipe-and-reinsert runs inside one BEGIN IMMEDIATE transaction, the
    same pattern app/allocation.py uses for advance_state — so a concurrent
    request (a surge tick, a dashboard poll) can't read or write in the
    middle of a reset and see a half-wiped database.
    """
    # Re-seeded first, before reset_all() or any other randomness this
    # reset might trigger, so every random decision from this point on
    # (tier assignment, injury, name, length of stay) follows the same
    # sequence given the same sequence of actions afterward.
    random.seed(RANDOM_SEED)
    reset_all()

    conn.execute("BEGIN IMMEDIATE")
    try:
        # Individual execute() calls, not executescript() — executescript()
        # implicitly commits any open transaction before running, which
        # would silently end the BEGIN IMMEDIATE above.
        conn.execute("DELETE FROM patients")
        conn.execute("DELETE FROM beds")
        conn.execute("DELETE FROM nurses")
        conn.execute("DELETE FROM physicians")
        conn.execute("DELETE FROM rooms")

        # Rooms, and BEDS_PER_ROOM beds in each.
        for room_name, room_type in ROOMS:
            room_id = conn.execute(
                "INSERT INTO rooms (name, room_type) VALUES (?, ?)", (room_name, room_type)
            ).lastrowid
            for bed_letter in BEDS_PER_ROOM:
                conn.execute(
                    "INSERT INTO beds (room_id, label, status) VALUES (?, ?, 'available')",
                    (room_id, f"{room_name} - Bed {bed_letter}"),
                )

        # Nurses and physicians, all starting available with nobody assigned yet.
        for name in NURSE_NAMES[:NURSE_COUNT]:
            conn.execute(
                "INSERT INTO nurses (name, status, max_patients, current_patients) VALUES (?, 'available', ?, 0)",
                (name, NURSE_MAX_PATIENTS),
            )
        for name in PHYSICIAN_NAMES[:PHYSICIAN_COUNT]:
            conn.execute(
                "INSERT INTO physicians (name, status, max_patients, current_patients) VALUES (?, 'available', ?, 0)",
                (name, PHYSICIAN_MAX_PATIENTS),
            )

        beds = conn.execute("SELECT id FROM beds ORDER BY id").fetchall()
        next_free_bed = 0

        for name, source, acuity, seconds_ago, in_bed in PATIENTS:
            arrival_dt = datetime.now() - timedelta(seconds=seconds_ago)
            arrival_time = arrival_dt.isoformat(timespec="seconds")

            bed_id = None
            nurse_id = None
            physician_id = None
            bed_assigned_at = None
            discharge_due_at = None
            status = "waiting"
            injury = random_injury(acuity)
            # Drawn once, here, and stored — not redrawn at placement time —
            # so the shadow baseline simulation's copy of this same patient
            # (see below) can be given the exact same length of stay.
            los_seconds = draw_los_seconds(acuity)

            if in_bed:
                bed_id = beds[next_free_bed]["id"]
                next_free_bed += 1
                conn.execute("UPDATE beds SET status = 'occupied' WHERE id = ?", (bed_id,))
                status = "in_bed"

                nurse_id = _assign_least_busy(conn, "nurses")
                physician_id = _assign_least_busy(conn, "physicians")

                bed_assigned_at = arrival_time
                discharge_due_at = (datetime.now() + timedelta(seconds=los_seconds)).isoformat(timespec="seconds")

            conn.execute(
                """
                INSERT INTO patients (
                    name, arrival_time, source, acuity, injury, los_seconds, status,
                    bed_id, nurse_id, physician_id, bed_assigned_at, discharge_due_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, arrival_time, source, acuity, injury, los_seconds, status,
                    bed_id, nurse_id, physician_id, bed_assigned_at, discharge_due_at,
                ),
            )
            baseline_simulation.arrive(acuity, arrival_dt, los_seconds, force_in_bed=in_bed)
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _assign_least_busy(conn, table):
    """Pick the staff member (nurse or physician) with the fewest current patients, and bump their count."""
    staff = conn.execute(
        f"SELECT id, current_patients, max_patients FROM {table} ORDER BY current_patients ASC LIMIT 1"
    ).fetchone()
    new_count = staff["current_patients"] + 1
    new_status = "busy" if new_count >= staff["max_patients"] else "available"
    conn.execute(
        f"UPDATE {table} SET current_patients = ?, status = ? WHERE id = ?",
        (new_count, new_status, staff["id"]),
    )
    return staff["id"]
