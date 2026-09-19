"""EDFlow patient arrivals — creates a new "waiting" patient for a simulated
kiosk check-in, ambulance arrival, or surge tick. All names are randomly
generated and fake; no real patient data is used.
"""

import random
from datetime import datetime

from app import baseline_simulation
from app.allocation import advance_state, draw_los_seconds
from app.config import ARRIVALS_PER_TICK_MAX, ARRIVALS_PER_TICK_MIN
from app.database import get_connection
from app.injuries import random_injury, random_tier

LAST_NAMES = [
    "Ramirez", "Bennett", "Coleman", "Diaz", "Ellis", "Fischer", "Garrison",
    "Holloway", "Ibrahim", "Jenkins", "Kowalski", "Lindqvist", "Martinez",
    "Nakamura", "Olsen", "Park", "Quinn", "Reyes", "Singh", "Thompson",
]
FIRST_INITIALS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def generate_patient_name():
    return f"Patient {random.choice(FIRST_INITIALS)}. {random.choice(LAST_NAMES)}"


def create_patient(source, name=None, acuity=None, injury=None):
    """Insert a new patient who has just arrived and is waiting to be placed in a bed."""
    conn = get_connection()

    tier = acuity or random_tier(source)
    injury = injury or random_injury(tier)
    name = name or generate_patient_name()
    arrival_dt = datetime.now()
    arrival_time = arrival_dt.isoformat(timespec="seconds")
    # Drawn once, here, at arrival — not redrawn later at placement time —
    # so the shadow baseline simulation's mirrored copy of this same
    # patient (see below) gets the exact same length of stay.
    los_seconds = draw_los_seconds(tier)

    cursor = conn.execute(
        """
        INSERT INTO patients (name, arrival_time, source, acuity, injury, los_seconds, status)
        VALUES (?, ?, ?, ?, ?, ?, 'waiting')
        """,
        (name, arrival_time, source, tier, injury, los_seconds),
    )
    conn.commit()
    patient_id = cursor.lastrowid

    # Mirror this exact arrival (same tier, same arrival time, same
    # length of stay) into the shadow baseline simulation — see
    # app/baseline_simulation.py — before advancing the real ED, so both
    # get an equivalent chance to place this patient on this same tick.
    baseline_simulation.arrive(tier, arrival_dt, los_seconds)

    # Immediately try to place this new arrival (and process any pending
    # discharges), so the response reflects their real status right away.
    advance_state(conn)

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    return dict(patient)


def run_arrival_tick(count=None):
    """Simulate one tick of a surge: `count` patients arrive at once,
    randomly split between ambulance and walk-in. `count` normally comes
    from app/surge.py's ramp profile (see POST /simulate/tick); it defaults
    to the old flat per-tick range here only so code that calls this
    directly without an active surge (existing tests) keeps working."""
    if count is None:
        count = random.randint(ARRIVALS_PER_TICK_MIN, ARRIVALS_PER_TICK_MAX)
    created = []
    for _ in range(count):
        source = random.choice(["walk-in", "ambulance"])
        created.append(create_patient(source))
    return created
