# EDFlow — Emergency Department Operations Dashboard

A hackathon project that simulates an ED's real-time operational state
(patients, staff, rooms, beds), calculates resource utilization, and flags
capacity bottlenecks — all with synthetic/mock data, no real hospital
integrations.

**Stack:** Python + FastAPI (backend and API) + SQLite (database) + plain
HTML/CSS/JavaScript (frontend), all served by one server.

**Live demo:** https://edflow-ilc9.onrender.com
(hosted on Render's free tier — it spins down after inactivity, so the
first load after a while can take up to 50 seconds to wake up)

## Setup (do this once)

```bash
cd /Users/arjunprabhune/Downloads/hackathon
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run it

```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

Then open your browser to:

- Dashboard: http://127.0.0.1:8000/
- Health check: http://127.0.0.1:8000/health
- Interactive API docs: http://127.0.0.1:8000/docs

## Optional: turning on the EDFlow Assistant

The chat widget (bottom-right corner) works without any setup — it just
replies with a friendly "not configured" message until you give it an API
key. To actually connect it to a model, set these environment variables
before starting the server (or as Environment Variables in Render's
dashboard for the live deployment):

```bash
export ASSISTANT_API_KEY="sk-..."          # required
export ASSISTANT_API_BASE="https://api.openai.com/v1"   # optional, this is the default
export ASSISTANT_MODEL="gpt-4o-mini"       # optional, this is the default
```

Any OpenAI-compatible chat-completions provider works — just point
`ASSISTANT_API_BASE` and `ASSISTANT_MODEL` at it. The key is read on the
server only (`app/assistant.py`) and is never sent to the browser.

Press `Ctrl+C` in the terminal to stop the server.

## Project status

This is being built step by step. So far:

- [x] Step 1: Backend skeleton — `/health` endpoint + placeholder dashboard page
- [x] Step 2: Database and seeded state (`GET /state`)
- [x] Step 3: Simulated check-in / ambulance event APIs
- [x] Step 4: Utilization and bottleneck (green/yellow/red) logic
- [x] Step 5: Real dashboard UI
- [x] Step 6: Live auto-updating dashboard (polling)
- [x] Step 7: Demo reset endpoint
- [x] Combined allocation engine: check-ins, ambulance arrivals, and time-based
      discharges all read/write the same shared state (patients, beds, rooms,
      nurses, physicians). Placement happens automatically when a bed + nurse
      + physician are all free; status and recommendations are derived from
      that same state.
- [x] Allocation recommendations side panel, driven by `GET /state`
- [x] Single "Run 30s Surge" simulator, tick-based departures, tier/injury
      priority system, realistic queue-based wait projection, load-aware
      nurse utilization, hospital float pool, and a "Patients This Run" panel

**Now underway: turning EDFlow into a resource-allocation decision tool.**
- [x] Phase 1: Headline breach metric, breach-driven status banner, per-tier
      breach breakdown table, readable status labels
- [x] Step 1: Fixed Reset Demo (it wasn't cancelling a running surge) and
      restructured resets around `app/reset_registry.py` so future stateful
      modules self-register instead of needing this endpoint edited again
- [x] Step 2: Three-tab navigation (Live Ops / Patients / Hospital Network) —
      a pure client-side show/hide, since simulation state lives on the
      backend and never resets when switching tabs
- [x] Step 3: Recommendations rewritten as a capped escalation ladder
      (ED's own resources → hospital float pool → overflow beds →
      relocation), every quantity capped at what's actually in the reserve
      (`app/reserve.py` now models nurses/physicians/rooms float pool and a
      separate overflow-beds pool)
- [x] Step 4: Reusable hospital model + two simulated nearby hospitals
      (Riverside Regional, Northgate Medical) and two urgent care sites,
      each drifting independently. New `GET /hospitals` (summary of every
      facility) and `GET /hospitals/{id}/state` (full home-ED-shaped state
      for the two hospitals) — all labeled `"simulated": true`, capacity/wait
      data only, never patient-level data
- [x] Step 5: Nearby Hospitals panel on Live Ops — appears directly under
      the status banner only when status is yellow/red, ranks facilities
      (never a red one) by capability match then wait time for whichever
      tier is currently breaching, marks the top pick "Recommended", and
      each card has a working "Get directions" Google Maps link
- [x] Step 6: Clicking a facility card opens a detail modal — full hospitals
      get the same headline metric, status banner, breach table, and
      widgets as the home ED (refreshing live via its own `/hospitals/{id}/state`
      poll); urgent care sites get their summary card instead. The modal
      reuses `renderStatusBanner()` / `renderBreachSummary()` /
      `renderHospitalWidgets()` with a `"modal-"` id prefix — same
      rendering code, not a duplicate implementation
- [x] Step 7: Hospital Network tab — a real Leaflet + OpenStreetMap map
      centered on the home ED, with a marker per facility (color-coded by
      live status) plus an "All Facilities" list beside it. Clicking a
      marker or a list card opens the exact same detail modal from Step 6 —
      same facility data, same view, not a separate implementation

**Feature additions** (chosen from a ranked list of 10 candidates, scored on
quality / mission fit / ease — see conversation for the full list):
- [x] **Apply / Apply All** — recommendations are no longer just advice.
      Clicking "Apply" on a rung 2/3 recommendation actually pulls the exact
      capped quantity out of the float/overflow pool and inserts real
      nurses/physicians/rooms/beds into the ED's own tables — capacity
      numbers change immediately, not just in the recommendation text.
      "Apply All" walks the ladder end to end.
- [x] **Relocation that actually affects the destination** — approving
      rung 4 moves real patients (never Tier 1, ambulance first, capability-
      and-wait-routed, never to a red facility) out of the home ED's queue
      and into a nearby facility's own simulated state — that facility's
      stats and status genuinely change as a result (`app/relocation.py`).
- [x] **Event log** — a timestamped feed (`app/event_log.py`) of every
      status transition and applied action, so a full demo run reads back
      as a story: breach detected → action applied → recovery. Lives on the
      Patients tab.
- [x] **Impact tab** (new 4th tab) — a session scorecard (peak avg wait,
      peak Tier 1-2 breaches, patients treated, last recovery time, LWBS
      rate — `app/scorecard.py` and `app/lwbs.py`) plus two live trend
      charts (avg wait and Tier 1-2 breaches over time), drawn on plain
      `<canvas>` from data already being polled — no charting library.
- [x] **How It Works tab** (new 5th tab) — a static explainer of the tier
      targets, the escalation ladder, the relocation rules, and the
      simulated/rules-based nature of the whole tool.
- [x] **Left-without-being-seen (LWBS)** — Tier 4-5 patients whose wait
      exceeds 2x their target eventually leave (`app/lwbs.py`), adding a
      real cost to inaction and feeding the Impact tab's LWBS rate.

**Shift handoff report + EDFlow Assistant:**
- [x] Shared session summary (`app/session_summary.py`, `GET
      /api/session-summary`) — one structured snapshot (duration, peaks
      with timestamps, breach/arrival/throughput counts, float/overflow
      usage, active relocation, nearby facility statuses, full event log)
      that both the situation report and the assistant read from, so
      neither can ever disagree with the dashboard or each other.
- [x] **Situation report** (Impact tab) — a "Generate situation report"
      button builds a full shift-handoff report from a fixed,
      deterministic template (no AI, no network call beyond the app's own
      API) — header, status, peaks, breaches by tier, arrivals/
      throughput, actions taken, outcome, handoff notes, and the full
      event log. Copy / Download (.txt) / Print, with a clean print
      stylesheet. Reset Demo clears it.
- [x] **EDFlow Assistant** — a floating chat widget (every tab) backed by
      `POST /api/assistant`. Answers only from the same shared session
      summary and live state (no patient-level data sent), suggests
      actions but never performs one, gives operational information only
      (never medical advice), and always names itself as simulated. The
      LLM call is isolated in one function (`app/assistant.py`) so the
      provider/model swap through environment variables alone; a missing
      key or a failed/slow call always falls back to a friendly message,
      never an error. Per-IP rate limited (`app/rate_limit.py`, 10/min,
      200/day) since it's the one endpoint that costs real money on a
      public site. Reset Demo clears the conversation.

**Fixed discharges, retuned capacity, and the shadow baseline comparison:**
- [x] **Per-patient length of stay** replaced the old fixed-tick/percentage
      discharge model, which pinned occupancy at 100% during a sustained
      surge and recovered only slowly afterward (`app/allocation.py`,
      `TIER_LOS_MINUTES` in `app/config.py`). Beds now go through a short
      cleaning/turnover delay before reuse.
- [x] **Float pool auto-return** — idle float staff/rooms and overflow beds
      return to the reserve automatically once occupancy drops below a
      threshold, after a short grace period so something isn't handed back
      the instant it's applied.
- [x] **Capacity retuned** — nurse/physician headcount cut from ~4.5x bed
      capacity down to just over 1x, so each resource can genuinely
      bottleneck in a heavy surge; float pool sizes increased ~50%; all of
      it moved into `app/config.py`.
- [x] **Shadow baseline simulation** (`app/baseline_simulation.py`) — a
      parallel, in-memory copy of the ED that gets the exact same arrivals
      but never any float pool, overflow, or relocation help, representing
      "this ED using only its own staff and beds." Fully deterministic
      (`random.seed(RANDOM_SEED)` on every reset) — the same sequence of
      actions after a reset produces identical results, every time.
- [x] **Impact of EDFlow** (`app/impact_metrics.py`) — a simulated,
      side-by-side comparison (avg wait, peak avg wait, Tier 1-2
      breach-minutes, total patient-minutes waited, LWBS) on the Impact tab
      and in the situation report, using the same definitions as the
      scorecard so the numbers reconcile everywhere.
- [x] **Verification suite** (`tests/test_simulation.py`) — see
      "Verification" below.

## Demo pacing tuned for a live run

- **Capacity**: 10 rooms / 30 beds, with nurse and physician headcount set
  to 10 and 7 (`NURSE_COUNT`/`PHYSICIAN_COUNT` in `app/config.py`) — sized
  just over bed capacity so beds, rooms, nurses, and physicians can each
  become the bottleneck in a heavy surge, not just beds.
- **Arrival rate**: `ARRIVALS_PER_TICK_MIN/MAX` in `app/config.py` is 2-4
  per tick, fast enough that a surge visibly builds up within seconds.
- **Discharge is per-patient, not a fixed batch** — see "Notes on the
  allocation engine" below; recovery after a surge is visibly fast because
  lengths of stay are short (`TIER_LOS_MINUTES`), not because of a fixed
  discharge-rate constant.
- **"Run Surge" is a manual on/off toggle**, not a fixed-duration timer —
  click to start, click again anytime to stop, and repeat as many times as
  you like.
- **"Avg wait (waiting patients)"** is a simple flat 5 minutes per patient
  in the queue (`app/logic.py`), not a realistic queue/service-rate
  projection — a "realistic" number stayed close to zero whenever beds
  were turning over quickly, which hid the recommendation ladder and
  Impact tab behind numbers too small to notice. This is explicitly a
  demo-legibility choice, not a claim of clinical accuracy.
- **Reset Demo** can be clicked any time, any number of times, including
  mid-surge — it always stops the surge and forces a full screen refresh,
  and the underlying database wipe/reseed runs as one atomic transaction
  (`app/seed.py`) so a surge tick can't land mid-reset.

## Notes on the nearby-facility network

- `app/facilities_config.py` is the single place to add a facility — name,
  type, coordinates, travel time/distance, capabilities, and a `base_load`
  the facility's simulated busyness drifts around. Nothing else needs to
  change to add one.
- The two full hospitals (`app/hospital_model.py`) reuse the exact same
  `get_status()` logic, tier targets, and threshold constants as the home
  ED, but track aggregate counts per tier instead of named patient rows —
  they don't need a patient list, just numbers.
- Each facility drifts on its own independent lazy clock (same "catch up
  based on elapsed real time" pattern as the home ED's discharge tick), so
  they naturally vary and don't all go red together.
- Resetting the home ED (`POST /demo/reset`) also resets every nearby
  facility, via the same `app/reset_registry.py` mechanism from Step 1.

## Notes on the allocation engine

- When a patient arrives (check-in, ambulance, or a surge tick), the system
  tries to place them immediately into a bed — but only if a bed **and** a
  nurse **and** a physician are all free. If any one of those is full, the
  patient stays "waiting" until capacity opens up, in **tier order** (Tier 1
  first, then longest-waiting within a tier).
- **Per-patient length of stay** (not a fixed shared schedule): each admitted
  patient is drawn a length of stay from `TIER_LOS_MINUTES` in
  `app/config.py` (higher tiers stay longer) the moment they arrive, and is
  discharged independently once it elapses — so the departure rate rises and
  falls naturally with how many patients are actually in beds, rather than
  being capped at a fixed batch size. Freed beds go through a short
  `BED_TURNOVER_MINUTES` "cleaning" delay before the next patient can use
  them. This all runs on every `GET /state` call (`app/allocation.py`), so
  simply leaving the dashboard open (2-second polling) is enough to see
  discharges and backfills happen on their own.
- **Float pool auto-return:** once bed occupancy drops below
  `FLOAT_RETURN_THRESHOLD_PCT`, idle float nurses/physicians/rooms and
  overflow beds are automatically handed back to the reserve — borrowed
  capacity doesn't linger once a surge has clearly passed. A
  `FLOAT_RETURN_GRACE_SECONDS` window stops something from being returned
  the instant it's applied, before it's ever had a chance to be used.
- **Compressed clock:** `SIM_MINUTES_PER_REAL_SECOND` in `app/config.py`
  treats 1 real second as 1 simulated minute (a 60x-faster clock), so the
  tier wait-time targets (Tier 1 immediate, Tier 2 10 min, ... Tier 5 120 min)
  translate into real waits of a few seconds to a couple of minutes — fast
  enough to see play out live in a demo.
- `app/config.py` is the single dial board for pacing: length of stay per
  tier, turnover/return timing, clock speed, tier targets, nurse/physician
  headcount and ratios, float pool sizes, and surge arrival rate.
- **Concurrency:** the surge (1 request/second) and the dashboard's polling
  (1 request/2 seconds) genuinely run at the same time, so `advance_state()`
  wraps its bed/staff assignment in a `BEGIN IMMEDIATE` transaction — this
  was tested under 50 simultaneous requests with zero double-booked beds.
- **If you pull or write further changes that alter `app/database.py`'s
  schema**, delete `edflow.db` and restart the server — it's just synthetic
  seed data and regenerates automatically; there's no real data to lose.

## Shadow baseline simulation and Impact of EDFlow

- `app/baseline_simulation.py` runs a second, in-memory-only copy of the ED
  alongside the real one: same arrivals (same tier, arrival time, and
  length of stay — drawn once and mirrored, never redrawn), same discharge
  logic, but it never receives a float nurse, an overflow bed, or a
  relocation. It represents "this ED using only its own staff and beds."
- `random.seed(RANDOM_SEED)` is re-applied at the start of every demo reset
  (`app/seed.py`), so the entire random sequence — tier, injury, name,
  length of stay — is fully deterministic from that point on: running the
  same sequence of actions after a reset produces identical results, every
  time (verified in `tests/test_simulation.py`).
- `app/impact_metrics.py` computes the head-to-head comparison (avg wait,
  peak avg wait, Tier 1-2 breach-minutes, total patient-minutes waited,
  left-without-being-seen) using the exact same formulas as the scorecard
  and breach summary, shown on the Impact tab and in the situation report's
  "Impact of EDFlow" section. If nothing has been applied yet, both are
  reported as identical rather than showing noise from two independently-
  ticking simulations as if it were meaningful.

## Verification

`tests/test_simulation.py` is a small automated suite (plain Python
`unittest`, no new dependency) covering the invariants from the discharge
fix, the float-pool auto-return, and the shadow baseline simulation. It
points `app.database` at its own temporary SQLite file, so it never
touches your real `edflow.db`. Run it with:

```bash
source venv/bin/activate
python3 -m unittest discover -s tests -v
```

It checks:
- [x] Bed occupancy never exceeds capacity or goes negative, under load.
- [x] After a surge ends, waiting reaches 0 and occupancy then declines.
- [x] Applied float pool / overflow resources return to the reserve.
- [x] The shadow baseline never grows past its fixed capacity and never
      relocates a patient — it has no code path that could.
- [x] The same seeded sequence of actions produces identical results twice.
- [x] Reset Demo clears everything, including the shadow baseline.

Manually confirmed reconciled end-to-end (scorecard, both trend charts,
event log, and the situation report's Outcome/Impact sections all showing
the same numbers for the same session, including "last recovery time"
completing with a real value instead of staying "in progress"):

- [x] Scorecard's peak avg wait / peak breaches / treated count match the
      situation report's "Peak during this session" and "Arrivals and
      throughput" sections exactly.
- [x] "Last recovery time" completes with a real number (e.g. `30.2s`) and
      that same number appears in the report's Outcome line.
- [x] The Impact tab card and the report's "Impact of EDFlow" section show
      the same headline and table for the same session.
- [x] The event log's entries match, in order, between the Patients tab,
      the Impact tab, and the report's Event Log section.
