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

# Ceiling for any wait-time ESTIMATE/PROJECTION formula (currently only
# app/hospital_model.py's nearby-hospital queue/service-rate projection —
# the home ED's own displayed wait is real elapsed time, which is already
# naturally bounded). Without a ceiling, a facility with very few beds
# occupied projects an arbitrarily long queue for its last waiting
# patient; design target is "never many hours," so this caps it there.
MAX_ESTIMATED_WAIT_MINUTES = 180

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

# Surge button: how many patients arrive on a tick that's actually
# generating arrivals. This is the per-tick range the ramp profile below
# scales by intensity — it is no longer used directly as "arrivals per
# second, forever" (see SURGE_DURATION_TICKS).
ARRIVALS_PER_TICK_MIN = 2
ARRIVALS_PER_TICK_MAX = 4

# --- Surge control (Step 1) ---
# The surge now runs for a fixed, server-enforced length rather than an
# indefinite client-side toggle: this many ticks. The dashboard drives one
# tick per second, so this is also a 30-second surge in real time — but
# counting ticks (not wall-clock time) keeps a run reproducible even if
# ticks land a little early or late, and lets tests run it without real
# sleeps.
SURGE_DURATION_TICKS = 30

# Total patients generated over one full surge, drawn once when the surge
# starts from the same seeded random stream as everything else, so the
# same seed always produces the same total. "Heavy" is the heaviest
# scenario referenced by the seeded verification tests (app/surge.py's
# start_surge(heavy=True)); the dashboard's single "Run Surge" button
# always uses the normal range.
SURGE_TOTAL_ARRIVALS_RANGE = (60, 80)
SURGE_HEAVY_TOTAL_ARRIVALS_RANGE = (110, 120)

# The heaviest scenario also needs a higher per-tick arrival rate, not
# just a higher total cap — at the normal ARRIVALS_PER_TICK_MIN/MAX rate,
# the ramp-up/peak/ramp-down profile naturally tops out around 60-65
# arrivals over SURGE_DURATION_TICKS regardless of how high the total cap
# is set, so a 110-120 cap would never actually bind. This rate is high
# enough that the heaviest scenario's arrivals budget runs out before the
# ramp-down finishes — the actual "hard cap" behavior Step 1 asked for.
ARRIVALS_PER_TICK_HEAVY_MIN = 5
ARRIVALS_PER_TICK_HEAVY_MAX = 8

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

# --- Hospital-wide reserves (Step 4 retune) ---
# Random (min, max) range for each reserve, re-rolled on every demo reset
# (app/reserve.py). Before this step: nurses (6, 15), physicians (2, 8),
# rooms (2, 6), overflow beds (3, 9) — roughly proportionate to the 10
# base nurses / 7 base physicians / 30 beds, but too small for the
# heaviest surge to lean on for long before exhausting them.
#
# After: roughly 3x at the low end (the worst-case floor a demo can
# actually land on, since these are randomized every reset) — a normal
# surge (SURGE_TOTAL_ARRIVALS_RANGE) should still only use part of this
# before recovering, while the heaviest surge (SURGE_HEAVY_TOTAL_ARRIVALS_
# RANGE) should be able to exhaust it and reach relocation. See Step 4's
# verification run for the actual before/after numbers.
FLOAT_POOL_NURSES_RANGE = (18, 26)  # ~20-24 extra nurses, on top of the 10-nurse base roster
FLOAT_POOL_PHYSICIANS_RANGE = (6, 9)  # ~6-8 extra physicians, on top of the 7-physician base roster
FLOAT_POOL_ROOMS_RANGE = (5, 7)  # ~6 extra rooms (each = FLOAT_ROOM_BED_COUNT beds), on top of the 10 base rooms
OVERFLOW_BEDS_RANGE = (12, 16)  # ~12-15 extra beds, on top of the 30-bed base capacity

# How long, in real seconds, a newly-applied float nurse/physician takes
# to actually arrive and start taking patients — they're pulled into the
# roster immediately (so the float pool count and capacity totals update
# right away) but can't be assigned to a patient until this elapses.
FLOAT_STAFF_ARRIVAL_DELAY_SECONDS = 8

# How many beds come with one "room" pulled from the float pool (rung 2) —
# used both when actually creating the room (app/apply_actions.py) and when
# estimating how many rooms would clear a given bed shortfall
# (app/logic.py's need-based recommendation sizing, Step 3).
FLOAT_ROOM_BED_COUNT = 2

# --- Recommendations (Step 3 retune) ---
# How far ahead, in simulated minutes, a projected Tier 1-2 breach must be
# before recommendations start proactively escalating — rather than only
# reacting once a breach has actually happened.
PROACTIVE_BREACH_WINDOW_MINUTES = 10
