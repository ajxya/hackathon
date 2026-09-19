"""EDFlow patient arrivals — creates a new "waiting" patient for a simulated
kiosk check-in, ambulance arrival, or surge tick. All names are randomly
generated and fake; no real patient data is used.
"""

import random
from datetime import datetime

from app.allocation import advance_state
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
    arrival_time = datetime.now().isoformat(timespec="seconds")

    cursor = conn.execute(
        """
        INSERT INTO patients (name, arrival_time, source, acuity, injury, status)
        VALUES (?, ?, ?, ?, ?, 'waiting')
        """,
        (name, arrival_time, source, tier, injury),
    )
    conn.commit()
    patient_id = cursor.lastrowid

    # Immediately try to place this new arrival (and process any pending
    # discharges), so the response reflects their real status right away.
    advance_state(conn)

    patient = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    conn.close()
    return dict(patient)


def run_arrival_tick():
    """Simulate one second of a surge: several patients arrive at once,
    randomly split between ambulance and walk-in."""
    count = random.randint(ARRIVALS_PER_TICK_MIN, ARRIVALS_PER_TICK_MAX)
    created = []
    for _ in range(count):
        source = random.choice(["walk-in", "ambulance"])
        created.append(create_patient(source))
    return created
