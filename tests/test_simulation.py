"""EDFlow verification suite (Step 6).

Covers the specific invariants from the fix-discharges / shadow-baseline /
impact-metrics work in this session:

- Occupancy never exceeds capacity or goes negative.
- After a surge ends, waiting reaches 0 and occupancy then declines
  toward baseline.
- Float/overflow resources that were applied return to the pool.
- The shadow baseline simulation never uses float, overflow, or
  relocation — its capacity never changes.
- The same seeded surge produces identical results twice.
- Reset Demo clears everything, including the baseline.

Plain stdlib unittest — no new dependency. Each test points app.database
at its own temporary SQLite file, so nothing here touches your real
edflow.db. A few tests wait on real timers (bed turnover, length of
stay, the float-return grace period) since the app itself has no
time-mocking hook; the whole suite normally finishes in well under two
minutes.

Run with:
    python3 -m unittest discover -s tests -v
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app import baseline_simulation, database
from app.allocation import advance_state
from app.apply_actions import apply_float_pool, apply_overflow_beds
from app.config import BEDS_TOTAL, NURSE_COUNT, PHYSICIAN_COUNT
from app.logic import get_breach_summary, get_recommendations, get_utilization
from app.patients import run_arrival_tick
from app.relocation import relocate_patients
from app.reserve import get_float_pool, get_overflow_pool
from app.seed import seed


class EDFlowSimulationTests(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="edflow_test_")
        self._original_db_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp_dir) / "test_edflow.db"
        database.init_db()
        self.conn = database.get_connection()
        seed(self.conn)

    def tearDown(self):
        self.conn.close()
        database.DB_PATH = self._original_db_path
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # ---- helpers ----------------------------------------------------

    def _bed_counts(self):
        return self.conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) AS occupied,
                SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
                SUM(CASE WHEN status = 'cleaning' THEN 1 ELSE 0 END) AS cleaning
            FROM beds
            """
        ).fetchone()

    def _assert_bed_accounting_consistent(self, row):
        total, occ, avail, cleaning = row["total"], row["occupied"], row["available"], row["cleaning"]
        self.assertGreaterEqual(occ, 0)
        self.assertGreaterEqual(avail, 0)
        self.assertGreaterEqual(cleaning, 0)
        self.assertLessEqual(occ, total)
        self.assertEqual(occ + avail + cleaning, total)

    def _waiting_count(self):
        return self.conn.execute("SELECT COUNT(*) AS c FROM patients WHERE status = 'waiting'").fetchone()["c"]

    def _run_surge(self, ticks):
        for _ in range(ticks):
            run_arrival_tick()
            advance_state(self.conn)

    # ---- invariant: bed accounting -----------------------------------

    def test_bed_accounting_never_exceeds_capacity_or_goes_negative(self):
        for _ in range(40):
            run_arrival_tick()
            advance_state(self.conn)
            self._assert_bed_accounting_consistent(self._bed_counts())

    # ---- invariant: recovery after a surge ---------------------------

    def test_surge_recovers_waiting_to_zero_then_occupancy_declines(self):
        self._run_surge(20)
        self.assertGreater(self._waiting_count(), 0, "surge should have produced a backlog to recover from")

        deadline = time.time() + 90
        reached_zero_waiting = False
        while time.time() < deadline:
            advance_state(self.conn)
            if self._waiting_count() == 0:
                reached_zero_waiting = True
                break
            time.sleep(1)
        self.assertTrue(reached_zero_waiting, "waiting never reached 0 within 90s of the surge ending")

        occupied_at_zero_wait = self._bed_counts()["occupied"]
        time.sleep(10)
        advance_state(self.conn)
        occupied_later = self._bed_counts()["occupied"]
        self.assertLessEqual(
            occupied_later, occupied_at_zero_wait, "occupancy should decline (or hold), not climb, once waiting is 0"
        )

    # ---- invariant: float/overflow resources return ------------------

    def test_float_and_overflow_resources_return_to_pool(self):
        before_float = get_float_pool()
        before_overflow = get_overflow_pool()

        apply_float_pool(self.conn, {"nurses": 1, "physicians": 1, "rooms": 1})
        apply_overflow_beds(self.conn, {"beds": 1})

        after_apply_float = get_float_pool()
        after_apply_overflow = get_overflow_pool()
        for key in after_apply_float:
            self.assertGreaterEqual(after_apply_float[key], 0)
            self.assertLessEqual(after_apply_float[key], before_float[key])
        self.assertLessEqual(
            after_apply_overflow["overflow_beds_available"], before_overflow["overflow_beds_available"]
        )

        # Bed occupancy is low right after a fresh seed, so once the
        # return grace period elapses these should come straight back.
        deadline = time.time() + 20
        returned = False
        while time.time() < deadline:
            advance_state(self.conn)
            pool = get_float_pool()
            overflow = get_overflow_pool()
            if pool == before_float and overflow == before_overflow:
                returned = True
                break
            time.sleep(1)
        self.assertTrue(returned, "float/overflow resources never returned to their pre-apply levels")

        for key, value in get_float_pool().items():
            self.assertGreaterEqual(value, 0)
        self.assertGreaterEqual(get_overflow_pool()["overflow_beds_available"], 0)

    # ---- invariant: baseline never uses float/overflow/relocation ----

    def test_baseline_never_uses_float_overflow_or_relocation(self):
        self._run_surge(15)

        # Exhaust the real ED's own reserves and relocate — the baseline
        # must be completely unaffected by all of it.
        utilization = get_utilization(self.conn)
        breach_summary = get_breach_summary(self.conn)
        recommendations = get_recommendations(utilization, breach_summary, get_float_pool(), get_overflow_pool())
        for rec in recommendations:
            if rec["rung"] == 2 and rec.get("quantities"):
                apply_float_pool(self.conn, rec["quantities"])
            elif rec["rung"] == 3 and rec.get("quantities"):
                apply_overflow_beds(self.conn, rec["quantities"])
        relocate_patients(self.conn)

        snapshot = baseline_simulation.get_snapshot()
        self.assertEqual(snapshot["beds_total"], BEDS_TOTAL, "baseline bed capacity must never change")

        for patient in baseline_simulation.get_patients():
            # The baseline's own capacity arrays are fixed-size at
            # NURSE_COUNT/PHYSICIAN_COUNT; a nurse/physician index outside
            # that range would mean it somehow grew, which should be
            # impossible since baseline_simulation.py has no apply/
            # relocate code path at all.
            if patient["nurse_index"] is not None:
                self.assertLess(patient["nurse_index"], NURSE_COUNT)
            if patient["physician_index"] is not None:
                self.assertLess(patient["physician_index"], PHYSICIAN_COUNT)
            self.assertNotEqual(patient["status"], "relocated")

    # ---- invariant: reproducibility -----------------------------------

    def _run_and_summarize(self, ticks):
        seed(self.conn)  # re-seeds random.seed(RANDOM_SEED) too — see app/seed.py
        self._run_surge(ticks)
        real = self.conn.execute(
            "SELECT status, acuity, COUNT(*) AS c FROM patients GROUP BY status, acuity ORDER BY status, acuity"
        ).fetchall()
        shadow_counts = baseline_simulation.get_snapshot()["status_counts"]
        return [dict(row) for row in real], shadow_counts

    def test_same_seeded_surge_produces_identical_results_twice(self):
        real_1, shadow_1 = self._run_and_summarize(15)
        real_2, shadow_2 = self._run_and_summarize(15)
        self.assertEqual(real_1, real_2, "the real ED's patient status/tier breakdown differed between two identical runs")
        self.assertEqual(shadow_1, shadow_2, "the shadow baseline's status breakdown differed between two identical runs")

    # ---- invariant: reset clears everything, including the baseline --

    def test_reset_clears_everything_including_baseline(self):
        self._run_surge(15)
        apply_float_pool(self.conn, {"nurses": 1, "physicians": 0, "rooms": 1})
        self.assertGreater(baseline_simulation.get_snapshot()["patients_total"], 6)

        seed(self.conn)  # what POST /demo/reset does under the hood

        real_total = self.conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]
        self.assertEqual(real_total, 6)
        snapshot = baseline_simulation.get_snapshot()
        self.assertEqual(snapshot["patients_total"], 6)
        self.assertEqual(snapshot["beds_total"], BEDS_TOTAL)
        self.assertEqual(get_float_pool()["rooms_available"] >= 0, True)  # sane, re-randomized, non-negative


if __name__ == "__main__":
    unittest.main()
