"""EDFlow simulated nearby hospital — a lightweight, aggregate-only model
reused for every nearby (non-home) hospital.

The home ED uses the full per-patient SQL engine (app/database.py,
app/allocation.py, etc.) because it needs named patients, injuries, and a
sortable patient list. Nearby hospitals don't need any of that — Step 6
only asks for the same AGGREGATE numbers and status logic as the home ED,
so this model tracks counts per tier instead of patient rows, and reuses
the exact same thresholds, tier targets, and get_status() logic as the
home ED so the two are directly comparable.
"""

import random
from datetime import datetime, timedelta

from app.config import (
    DISCHARGE_FRACTION,
    DISCHARGE_TICK_SECONDS,
    MAX_ESTIMATED_WAIT_MINUTES,
    NURSE_TRIAGE_WEIGHT,
    PHYSICIAN_TRIAGE_WEIGHT,
    SIM_MINUTES_PER_REAL_SECOND,
    TIER_TARGET_MINUTES,
)
from app.injuries import random_tier
from app.logic import get_status

DRIFT_TICK_SECONDS = 4


def _pct(used, total):
    if not total:
        return 0.0
    return round(used / total * 100, 1)


class SimulatedHospital:
    """One nearby hospital's aggregate state, drifting on its own clock."""

    def __init__(self, facility):
        self.facility = facility
        self.beds_total = 8
        self.rooms_total = 4
        self.nurses_capacity_total = 20
        self.physicians_capacity_total = 15
        self._next_tick_at = None
        self._reset_state()

    def _reset_state(self):
        base = self.facility["base_load"]
        self.beds_occupied = round(self.beds_total * base)
        self.rooms_in_use = min(self.rooms_total, max(1, round(self.rooms_total * base)))
        self.nurses_used = round(self.nurses_capacity_total * base)
        self.physicians_used = round(self.physicians_capacity_total * base)
        self.waiting_by_tier = {tier: 0 for tier in range(1, 6)}
        for _ in range(random.randint(0, 3)):
            self.waiting_by_tier[random_tier("walk-in")] += 1
        self.arrivals_ambulance = random.randint(5, 20)
        self.arrivals_walk_in = random.randint(10, 30)
        self.float_pool = {
            "nurses_available": random.randint(3, 8),
            "physicians_available": random.randint(1, 4),
            "rooms_available": random.randint(0, 3),
        }
        self._next_tick_at = None

    def _advance(self):
        """Simulate whatever real time has passed since the last check —
        this is a lazy/on-demand simulation like the home ED's, not a real
        background thread, so it catches up whenever it's next queried."""
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
        base = self.facility["base_load"]

        discharge_count = round(self.beds_occupied * DISCHARGE_FRACTION)
        self.beds_occupied = max(0, self.beds_occupied - discharge_count)
        self.nurses_used = max(0, self.nurses_used - discharge_count)
        self.physicians_used = max(0, self.physicians_used - discharge_count)

        # Random arrivals biased toward this facility's own base load, so it
        # oscillates around a characteristic busyness instead of trending to
        # zero or to permanent gridlock — and each facility drifts
        # independently, so they don't all spike red at once.
        arrival_chance = max(base + random.uniform(-0.25, 0.25), 0.05)
        if random.random() < arrival_chance:
            source = random.choice(["walk-in", "ambulance"])
            tier = random_tier(source)
            self.waiting_by_tier[tier] += 1
            if source == "ambulance":
                self.arrivals_ambulance += 1
            else:
                self.arrivals_walk_in += 1

        for tier in range(1, 6):
            while self.waiting_by_tier[tier] > 0 and self._has_capacity():
                self.waiting_by_tier[tier] -= 1
                self.beds_occupied += 1
                self.nurses_used += 1
                self.physicians_used += 1
                if random.random() < 0.3:
                    self.rooms_in_use = min(self.rooms_total, self.rooms_in_use + 1)

    def _has_capacity(self):
        return (
            self.beds_occupied < self.beds_total
            and self.nurses_used < self.nurses_capacity_total
            and self.physicians_used < self.physicians_capacity_total
        )

    def receive_relocated_patient(self, tier):
        """A patient diverted from the home ED joins this facility's own
        waiting queue — its stats and status change as a real consequence,
        not just a home-ED bookkeeping entry."""
        self.waiting_by_tier[tier] += 1

    def _estimate_avg_wait(self, patients_waiting):
        """A projection, not a real elapsed-time measurement — this facility
        is aggregate-only (no named patients to time), so this is the one
        legitimate use of a queue-length / service-rate estimate left in the
        app. Clamped to MAX_ESTIMATED_WAIT_MINUTES so a facility with very
        few beds occupied can't project an arbitrarily long wait for its
        last waiting patient."""
        if not patients_waiting:
            return 0.0

        # DISCHARGE_TICK_SECONDS is a fixed positive constant, so this is
        # never a near-zero division — it's exactly 0 (guarded below) or
        # bounded below by DISCHARGE_FRACTION / DISCHARGE_TICK_SECONDS.
        service_rate_per_sec = (
            (self.beds_occupied * DISCHARGE_FRACTION) / DISCHARGE_TICK_SECONDS if self.beds_occupied else 0
        )
        if service_rate_per_sec > 0:
            projected = [
                ((i + 1) / service_rate_per_sec) * SIM_MINUTES_PER_REAL_SECOND for i in range(patients_waiting)
            ]
            estimate = sum(projected) / len(projected)
        else:
            estimate = patients_waiting * 5.0

        return round(min(estimate, MAX_ESTIMATED_WAIT_MINUTES), 1)

    def _breach_summary(self):
        total_waiting = sum(self.waiting_by_tier.values())
        estimate = self._estimate_avg_wait(total_waiting)

        by_tier = {}
        for tier in range(1, 6):
            waiting = self.waiting_by_tier[tier]
            target = TIER_TARGET_MINUTES.get(tier, 0)
            avg = estimate if waiting else 0.0
            by_tier[tier] = {"waiting": waiting, "breached": waiting if avg > target else 0, "avg_wait_minutes": avg}

        return {
            "tier1_2_breaches": by_tier[1]["breached"] + by_tier[2]["breached"],
            "total_breaches": sum(t["breached"] for t in by_tier.values()),
            "by_tier": by_tier,
        }

    def get_state(self):
        """Same response shape as the home ED's GET /state (minus the
        patient-level list, which nearby hospitals don't track)."""
        self._advance()

        patients_waiting = sum(self.waiting_by_tier.values())
        nurse_workload_units = self.nurses_used + patients_waiting * NURSE_TRIAGE_WEIGHT
        physician_workload_units = self.physicians_used + patients_waiting * PHYSICIAN_TRIAGE_WEIGHT

        utilization = {
            "beds": {
                "total": self.beds_total,
                "occupied": self.beds_occupied,
                "available": self.beds_total - self.beds_occupied,
                "cleaning": 0,
                "pct": _pct(self.beds_occupied, self.beds_total),
            },
            "rooms": {
                "total": self.rooms_total,
                "in_use": self.rooms_in_use,
                "pct": _pct(self.rooms_in_use, self.rooms_total),
            },
            "nurses": {
                "capacity_used": min(round(nurse_workload_units), self.nurses_capacity_total),
                "capacity_total": self.nurses_capacity_total,
                "pct": min(_pct(nurse_workload_units, self.nurses_capacity_total), 100.0),
            },
            "physicians": {
                "capacity_used": min(round(physician_workload_units), self.physicians_capacity_total),
                "capacity_total": self.physicians_capacity_total,
                "pct": min(_pct(physician_workload_units, self.physicians_capacity_total), 100.0),
            },
            "patients_waiting": patients_waiting,
            "avg_wait_minutes": self._estimate_avg_wait(patients_waiting),
        }

        breach_summary = self._breach_summary()
        status = get_status(utilization, breach_summary)

        return {
            "id": self.facility["id"],
            "name": self.facility["name"],
            "type": self.facility["type"],
            "capabilities": self.facility["capabilities"],
            "travel_time_minutes": self.facility["travel_time_minutes"],
            "distance_miles": self.facility["distance_miles"],
            "lat": self.facility["lat"],
            "lng": self.facility["lng"],
            "simulated": True,
            "patients": {
                "total": self.beds_occupied + patients_waiting,
                "waiting": patients_waiting,
                "in_bed": self.beds_occupied,
                "avg_wait_minutes": utilization["avg_wait_minutes"],
            },
            "utilization": utilization,
            "breach_summary": breach_summary,
            "status": status,
            "arrivals": {"ambulance": self.arrivals_ambulance, "walk_in": self.arrivals_walk_in},
            "float_pool": self.float_pool,
        }
