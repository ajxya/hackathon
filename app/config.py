"""EDFlow simulation tuning constants — the dial board.

If you want the demo to run faster/slower, or to rebalance how
aggressively the ED admits, discharges, or triages patients, this is the
one file to edit.
"""

# How often a batch of patients is discharged (seconds), and what fraction
# of currently-occupied beds are freed up each time.
DISCHARGE_TICK_SECONDS = 4
DISCHARGE_FRACTION = 0.25

# The simulated clock's speed relative to real time: 1 real second counts
# as this many simulated minutes when comparing a patient's wait against
# the tier targets below. (1 => the clock runs 60x faster than real life,
# so a 30-minute target becomes a 30-real-second target.)
SIM_MINUTES_PER_REAL_SECOND = 1

# Target wait time (in simulated minutes) before a patient of each tier is
# considered overdue. Tier 1 = needs a bed immediately.
TIER_TARGET_MINUTES = {1: 0, 2: 10, 3: 30, 4: 60, 5: 120}

# How much extra workload each waiting patient adds to nurses/physicians,
# on top of the patients they're actively treating in a bed — nurses triage
# and monitor the waiting room; physicians periodically reassess it too,
# just less intensively. Without this, both would be capped at whatever
# fraction of the (fixed) bed count they happen to staff, and would look
# "stuck" no matter how large the waiting queue grows.
NURSE_TRIAGE_WEIGHT = 0.3
PHYSICIAN_TRIAGE_WEIGHT = 0.15

# Surge button: how many patients arrive per simulated second. The surge
# itself now runs indefinitely (start/stop toggle in the UI) rather than a
# fixed duration, so there's no "how long it lasts" constant here anymore.
ARRIVALS_PER_TICK_MIN = 1
ARRIVALS_PER_TICK_MAX = 3
