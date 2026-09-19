"""Shared mock/synthetic data generators for the ED dashboard.

All data here is fabricated for demo purposes and regenerated deterministically
(seeded) so the app looks consistent across pages within a session.
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ---- Fixed color roles (categorical identity stays consistent across the app) ----
STATUS_COLORS = {
    "good": "#1E8E5A",       # available / on track
    "warning": "#B8860B",    # elevated / watch
    "serious": "#C9622A",    # boarding / overloaded
    "critical": "#B3261E",   # critical / offline
}

ACUITY_COLORS = {
    1: "#B3261E",  # ESI 1 - resuscitation
    2: "#C9622A",  # ESI 2 - emergent
    3: "#B8860B",  # ESI 3 - urgent
    4: "#3B7DD8",  # ESI 4 - less urgent
    5: "#1E8E5A",  # ESI 5 - non-urgent
}

ROOM_STATUS_COLORS = {
    "Occupied": "#B3261E",
    "Boarding": "#C9622A",
    "Cleaning": "#B8860B",
    "Available": "#1E8E5A",
}

SEED = 42


def _rng(seed_offset: int = 0) -> np.random.Generator:
    return np.random.default_rng(SEED + seed_offset)


def hourly_volume(hours: int = 24) -> pd.DataFrame:
    """Simulated patient arrivals per hour with an evening peak."""
    rng = _rng(1)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    timestamps = [now - timedelta(hours=hours - 1 - i) for i in range(hours)]
    hour_of_day = np.array([t.hour for t in timestamps])
    base = 6 + 5 * np.sin((hour_of_day - 8) / 24 * 2 * np.pi) + 4 * np.exp(-((hour_of_day - 19) ** 2) / 18)
    arrivals = np.clip(base + rng.normal(0, 1.3, size=hours), 1, None).round().astype(int)
    return pd.DataFrame({"timestamp": timestamps, "arrivals": arrivals})


def wait_time_trend(hours: int = 24) -> pd.DataFrame:
    """Simulated median door-to-provider wait time (minutes) per hour."""
    rng = _rng(2)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    timestamps = [now - timedelta(hours=hours - 1 - i) for i in range(hours)]
    hour_of_day = np.array([t.hour for t in timestamps])
    base = 35 + 25 * np.exp(-((hour_of_day - 20) ** 2) / 20)
    wait = np.clip(base + rng.normal(0, 4, size=hours), 10, None).round(1)
    return pd.DataFrame({"timestamp": timestamps, "wait_minutes": wait})


def current_metrics() -> dict:
    rng = _rng(3)
    return {
        "census": int(rng.integers(58, 74)),
        "capacity": 80,
        "avg_wait_min": round(float(rng.uniform(38, 62)), 1),
        "boarding_count": int(rng.integers(6, 16)),
        "staffed_nurses": int(rng.integers(14, 19)),
        "left_without_being_seen_pct": round(float(rng.uniform(1.5, 4.5)), 1),
    }


def alerts() -> list[dict]:
    metrics = current_metrics()
    alerts_list = []
    if metrics["census"] / metrics["capacity"] > 0.85:
        alerts_list.append({"level": "critical", "text": "ED at >85% capacity — consider surge protocol."})
    if metrics["boarding_count"] >= 10:
        alerts_list.append({"level": "serious", "text": f"{metrics['boarding_count']} patients boarding, awaiting inpatient beds."})
    if metrics["avg_wait_min"] > 50:
        alerts_list.append({"level": "warning", "text": "Median wait time trending above 50 minutes."})
    if not alerts_list:
        alerts_list.append({"level": "good", "text": "All systems within normal operating thresholds."})
    return alerts_list


def nurse_roster(n: int = 16) -> pd.DataFrame:
    rng = _rng(4)
    names = [
        "A. Rivera", "B. Chen", "C. Okafor", "D. Novak", "E. Haddad", "F. Kowalski",
        "G. Singh", "H. Marsh", "I. Torres", "J. Blake", "K. Petrov", "L. Nakamura",
        "M. Dubois", "N. Osei", "O. Fischer", "P. Alvarez",
    ][:n]
    zones = rng.choice(["Resus", "Acute", "Fast Track", "Peds"], size=n, p=[0.15, 0.4, 0.3, 0.15])
    patient_load = rng.integers(1, 7, size=n)
    max_load = np.where(zones == "Resus", 2, np.where(zones == "Fast Track", 5, 4))
    df = pd.DataFrame({
        "nurse": names,
        "zone": zones,
        "patients_assigned": patient_load,
        "max_ratio": max_load,
    })
    df["load_pct"] = (df["patients_assigned"] / df["max_ratio"] * 100).round(0)
    return df


def bed_grid(rows: int = 5, cols: int = 8) -> pd.DataFrame:
    rng = _rng(5)
    n = rows * cols
    statuses = rng.choice(
        ["Available", "Occupied", "Boarding", "Cleaning"],
        size=n,
        p=[0.22, 0.45, 0.18, 0.15],
    )
    turnaround = np.where(
        statuses == "Cleaning", rng.integers(5, 45, size=n),
        np.where(statuses == "Boarding", rng.integers(30, 240, size=n), 0),
    )
    room_ids = [f"R{r+1}{chr(65+c)}" for r in range(rows) for c in range(cols)]
    zones = (["Resus"] * (cols) + ["Acute"] * (cols * 2) + ["Fast Track"] * (cols * 1) + ["Peds"] * (cols))[:n]
    return pd.DataFrame({
        "room": room_ids,
        "zone": zones,
        "status": statuses,
        "minutes_in_status": turnaround,
        "row": [r for r in range(rows) for _ in range(cols)],
        "col": list(range(cols)) * rows,
    })
