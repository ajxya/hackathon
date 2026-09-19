"""EDFlow simulation tuning constants — the dial board.

If you want the demo to run faster/slower, or to rebalance how
aggressively the ED admits, discharges, or triages patients, this is the
one file to edit.
"""

# Seeded at the start of every demo reset (app/seed.py), so every random
# decision after that point (tier assignment, injury, name, length of
# stay) follows the exact same sequence given the exact same sequence of
# actions — running the same seeded surge twice produces identical
# results. The shadow baseline simulation (app/baseline_simulation.py)
# never draws its own randomness; it only ever mirrors values the real ED
# already generated, so this one seed governs both.
RANDOM_SEED = 42

# How often a batch of patients is discharged (seconds), and what fraction
# of currently-occupied beds are freed up each time. Used only by the
# nearby-hospital simulation (app/hospital_model.py), which tracks
# aggregate bed counts, not named patients, so it has no per-patient
# length of stay to key off. The home ED's own discharge logic no longer
# uses these — see TIER_LOS_MINUTES below.
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

# Length of stay once a patient is actually placed in a bed, drawn from a
# per-tier (min, max) range in simulated minutes — the same clock as
# TIER_TARGET_MINUTES above. More severe tiers get more involved care and
# stay longer; Tier 5 patients are in and out quickly. This drives the
# home ED's discharge process (app/allocation.py): each admitted patient
# is discharged independently when their own stay elapses, not on any
# fixed shared schedule, so the departure rate rises and falls naturally
# with how many patients are actually in beds.
TIER_LOS_MINUTES = {
    1: (20, 35),
    2: (16, 28),
    3: (12, 20),
    4: (8, 14),
    5: (5, 10),
}

# How long a bed (and, by extension, its room) stays in "cleaning" after a
# discharge before it can take the next patient — a short turnover delay,
# in the same simulated-minutes clock as everything else above.
BED_TURNOVER_MINUTES = 2

# Once bed occupancy falls below this percentage, any float nurses/
# physicians/rooms and overflow beds that are currently idle (not
# assigned to anyone) are returned to the float pool automatically —
# borrowed capacity doesn't linger once a surge has clearly passed.
FLOAT_RETURN_THRESHOLD_PCT = 50

# Minimum real seconds a float resource must exist before it's eligible
# for that automatic return — without this, a float nurse/room applied at
# a moment when occupancy happens to already be under the threshold above
# would get handed straight back before ever being usable.
FLOAT_RETURN_GRACE_SECONDS = 10

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
ARRIVALS_PER_TICK_MIN = 2
ARRIVALS_PER_TICK_MAX = 4

# --- Capacity (Step 3-4) ---
# The home ED's total bed count — must match app/seed.py's ROOMS list x
# BEDS_PER_ROOM (10 rooms x 3 beds). Kept here too, as a plain number, so
# the shadow baseline simulation (app/baseline_simulation.py) can use the
# same capacity without importing app/seed.py, which would create a
# circular import (seed.py mirrors every seeded patient into the baseline
# simulation as it inserts them).
BEDS_TOTAL = 30

# --- Staffing (Step 3 retune) ---
# Sized relative to the 30-bed / 10-room capacity above so that beds,
# rooms, nurses, and physicians can EACH become the bottleneck in a heavy
# surge, instead of nurses/physicians having so much spare capacity that
# only beds ever fill up. app/seed.py uses the first NURSE_COUNT/
# PHYSICIAN_COUNT names from its own name lists.
NURSE_COUNT = 10
NURSE_MAX_PATIENTS = 4  # -> 40 nurse-slots total, just above the 30 beds
PHYSICIAN_COUNT = 7
PHYSICIAN_MAX_PATIENTS = 6  # -> 42 physician-slots total

# --- Hospital-wide reserves (Step 3 retune) ---
# Random (min, max) range for each reserve, re-rolled on every demo reset
# (app/reserve.py) — about 50% larger than the previous ranges, so a heavy
# surge has more room to lean on outside help before needing relocation.
FLOAT_POOL_NURSES_RANGE = (6, 15)
FLOAT_POOL_PHYSICIANS_RANGE = (2, 8)
FLOAT_POOL_ROOMS_RANGE = (2, 6)
OVERFLOW_BEDS_RANGE = (3, 9)

# How long, in real seconds, a newly-applied float nurse/physician takes
# to actually arrive and start taking patients — they're pulled into the
# roster immediately (so the float pool count and capacity totals update
# right away) but can't be assigned to a patient until this elapses.
FLOAT_STAFF_ARRIVAL_DELAY_SECONDS = 8
