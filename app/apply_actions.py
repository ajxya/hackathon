"""EDFlow "Apply" actions — turning a recommendation into a real change.

Each function here takes the exact capped quantities already computed by
get_recommendations() (so there's one source of truth for "how much," not
two) and actually writes new capacity into the ED's own tables, decrementing
the reserve it came from.
"""

import random
from datetime import datetime

from app.allocation import advance_state
from app.config import FLOAT_ROOM_BED_COUNT, NURSE_MAX_PATIENTS, PHYSICIAN_MAX_PATIENTS
from app.reserve import decrement_float_pool, decrement_overflow_pool

_SUFFIX_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _random_suffix():
    return "".join(random.choice(_SUFFIX_CHARS) for _ in range(3))


def apply_float_pool(conn, quantities):
    """Rung 2: actually pull nurses/physicians/rooms out of the float pool
    and into the ED's own roster."""
    nurses_n = quantities.get("nurses", 0)
    physicians_n = quantities.get("physicians", 0)
    rooms_n = quantities.get("rooms", 0)
    now = datetime.now().isoformat(timespec="seconds")

    for _ in range(nurses_n):
        conn.execute(
            "INSERT INTO nurses (name, status, max_patients, current_patients, source, added_at) "
            "VALUES (?, 'available', ?, 0, 'float', ?)",
            (f"Float Nurse {_random_suffix()}", NURSE_MAX_PATIENTS, now),
        )
    for _ in range(physicians_n):
        conn.execute(
            "INSERT INTO physicians (name, status, max_patients, current_patients, source, added_at) "
            "VALUES (?, 'available', ?, 0, 'float', ?)",
            (f"Float Dr. {_random_suffix()}", PHYSICIAN_MAX_PATIENTS, now),
        )
    for _ in range(rooms_n):
        room_id = conn.execute(
            "INSERT INTO rooms (name, room_type) VALUES (?, 'Float')", (f"Float Room {_random_suffix()}",)
        ).lastrowid
        for bed_letter in ("A", "B", "C", "D")[:FLOAT_ROOM_BED_COUNT]:
            conn.execute(
                "INSERT INTO beds (room_id, label, status, created_at) VALUES (?, ?, 'available', ?)",
                (room_id, f"Float Room {_random_suffix()} - Bed {bed_letter}", now),
            )

    decrement_float_pool(nurses=nurses_n, physicians=physicians_n, rooms=rooms_n)

    # Give the newly added capacity an immediate chance to admit waiting
    # patients, rather than waiting for the next poll.
    advance_state(conn)

    return nurses_n, physicians_n, rooms_n


def _get_or_create_overflow_room(conn):
    row = conn.execute("SELECT id FROM rooms WHERE room_type = 'Overflow'").fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO rooms (name, room_type) VALUES ('Overflow Bay', 'Overflow')").lastrowid


def apply_overflow_beds(conn, quantities):
    """Rung 3: actually stand up extra physical beds within the ED."""
    beds_n = quantities.get("beds", 0)
    if beds_n <= 0:
        return 0

    room_id = _get_or_create_overflow_room(conn)
    now = datetime.now().isoformat(timespec="seconds")
    for _ in range(beds_n):
        conn.execute(
            "INSERT INTO beds (room_id, label, status, created_at) VALUES (?, ?, 'available', ?)",
            (room_id, f"Overflow Bed {_random_suffix()}", now),
        )

    decrement_overflow_pool(beds=beds_n)
    advance_state(conn)

    return beds_n
