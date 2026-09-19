# EDFlow — Emergency Department Operations Dashboard

A hackathon project that simulates an ED's real-time operational state
(patients, staff, rooms, beds), calculates resource utilization, and flags
capacity bottlenecks — all with synthetic/mock data, no real hospital
integrations.

**Stack:** Python + FastAPI (backend and API) + SQLite (database) + plain
HTML/CSS/JavaScript (frontend), all served by one server.

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

## Demo pacing tuned for a live run

- **Capacity**: 10 rooms / 30 beds (up from 5/10), with nurse and physician
  headcount scaled up to match (18 nurses, 9 physicians) so staffing
  doesn't become an artificial bottleneck the moment beds grew.
- **Arrival rate slowed**: `ARRIVALS_PER_TICK_MIN/MAX` in `app/config.py`
  dropped from 2-5/tick to 1-3/tick, so a surge builds up gradually instead
  of saturating the ED in a few seconds.
- **Discharge rate sped up**: `DISCHARGE_TICK_SECONDS` 5→4 and
  `DISCHARGE_FRACTION` 0.2→0.25, so recovery after a surge is visibly
  faster too.
- **"Run Surge" is now a manual on/off toggle**, not a fixed 30-second
  timer — click to start, click again anytime to stop, and repeat as many
  times as you like. This gives full control over exactly how long a surge
  runs during a live demo.

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
- Every `DISCHARGE_TICK_SECONDS` (5s by default — see `app/config.py`), a
  fraction of currently-admitted patients (oldest-admitted first) are
  discharged, freeing their bed/nurse/physician back up for the next waiting
  patient. This check runs on every `GET /state` call, so simply leaving the
  dashboard open (2-second polling) is enough to see discharges and backfills
  happen on their own.
- **Compressed clock:** `SIM_MINUTES_PER_REAL_SECOND` in `app/config.py`
  treats 1 real second as 1 simulated minute (a 60x-faster clock), so the
  tier wait-time targets (Tier 1 immediate, Tier 2 10 min, ... Tier 5 120 min)
  translate into real waits of a few seconds to a couple of minutes — fast
  enough to see play out live in a demo.
- `app/config.py` is the single dial board for pacing: discharge rate,
  clock speed, tier targets, nurse triage weighting, and surge arrival rate.
- **Concurrency:** the surge (1 request/second) and the dashboard's polling
  (1 request/2 seconds) genuinely run at the same time, so `advance_state()`
  wraps its bed/staff assignment in a `BEGIN IMMEDIATE` transaction — this
  was tested under 50 simultaneous requests with zero double-booked beds.
- **If you pull or write further changes that alter `app/database.py`'s
  schema**, delete `edflow.db` and restart the server — it's just synthetic
  seed data and regenerates automatically; there's no real data to lose.
