"""EDFlow nearby-facility network — the home ED plus simulated nearby
hospitals and urgent care sites.

All facility data here is simulated/fictional. Only capacity and wait
data is ever shared for these facilities — never patient-level data (that
stays in the home ED's own database).
"""

import random
from datetime import datetime, timedelta

from app.facilities_config import FACILITIES
from app.hospital_model import SimulatedHospital
from app.reset_registry import register_reset

DRIFT_TICK_SECONDS = 6


class UrgentCareSite:
    """A lightweight facility: summary data only, no tier-level breakdown —
    used mainly as a redirect target for low-tier patients."""

    def __init__(self, facility):
        self.facility = facility
        self.beds_total = 6
        self._next_tick_at = None
        self._reset_state()

    def _reset_state(self):
        base = self.facility["base_load"]
        self.beds_available = max(0, round(self.beds_total * (1 - base)))
        self.avg_wait_minutes = round(10 + base * 40 + random.uniform(-5, 10), 1)
        self._next_tick_at = None

    def _advance(self):
        now = datetime.now()
        if self._next_tick_at is None:
            self._next_tick_at = now + timedelta(seconds=DRIFT_TICK_SECONDS)
            return
        ticks_run = 0
        while now >= self._next_tick_at and ticks_run < 10:
            self._drift_step()
            self._next_tick_at += timedelta(seconds=DRIFT_TICK_SECONDS)
            ticks_run += 1

    def _drift_step(self):
        self.beds_available = max(0, min(self.beds_total, self.beds_available + random.choice([-1, 0, 0, 1])))
        self.avg_wait_minutes = max(2.0, round(self.avg_wait_minutes + random.uniform(-4, 4), 1))

    def get_summary(self):
        self._advance()
        if self.avg_wait_minutes >= 60 or self.beds_available == 0:
            status = "red"
        elif self.avg_wait_minutes >= 30:
            status = "yellow"
        else:
            status = "green"
        return {
            "id": self.facility["id"],
            "name": self.facility["name"],
            "type": self.facility["type"],
            "status": status,
            "avg_wait_minutes": self.avg_wait_minutes,
            "beds_available": self.beds_available,
            "travel_time_minutes": self.facility["travel_time_minutes"],
            "distance_miles": self.facility["distance_miles"],
            "capabilities": self.facility["capabilities"],
            "lat": self.facility["lat"],
            "lng": self.facility["lng"],
            "simulated": True,
        }


_hospitals = {}
_urgent_cares = {}


def _init_facilities():
    _hospitals.clear()
    _urgent_cares.clear()
    for facility in FACILITIES:
        if facility["type"] == "hospital":
            _hospitals[facility["id"]] = SimulatedHospital(facility)
        else:
            _urgent_cares[facility["id"]] = UrgentCareSite(facility)


_init_facilities()


@register_reset
def reset_network():
    _init_facilities()


def get_hospital(hospital_id):
    """Returns a SimulatedHospital for one of the two full nearby
    hospitals, or None if the id doesn't match one (including if it's an
    urgent care site, which only has summary data)."""
    return _hospitals.get(hospital_id)


def get_urgent_care(facility_id):
    return _urgent_cares.get(facility_id)


def get_all_facilities_summary():
    """Summary for GET /hospitals — every facility's headline info, never
    patient-level data."""
    summaries = []

    for hospital in _hospitals.values():
        state = hospital.get_state()
        summaries.append(
            {
                "id": state["id"],
                "name": state["name"],
                "type": state["type"],
                "status": state["status"]["level"],
                "avg_wait_minutes": state["utilization"]["avg_wait_minutes"],
                "estimated_wait_by_tier": {
                    tier: data["avg_wait_minutes"] for tier, data in state["breach_summary"]["by_tier"].items()
                },
                "beds_available": state["utilization"]["beds"]["available"],
                "travel_time_minutes": state["travel_time_minutes"],
                "distance_miles": state["distance_miles"],
                "capabilities": state["capabilities"],
                "lat": state["lat"],
                "lng": state["lng"],
                "simulated": True,
            }
        )

    for urgent_care in _urgent_cares.values():
        summaries.append(urgent_care.get_summary())

    return summaries
