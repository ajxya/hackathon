"""WayPoint Allocations backend — the FastAPI server.

Run with:  uvicorn app.main:app --reload
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import baseline_simulation
from app.allocation import advance_state
from app.apply_actions import apply_float_pool, apply_overflow_beds
from app.assistant import get_assistant_reply
from app.database import get_connection, init_db
from app.event_log import get_events, log_event, note_status
from app.facilities_config import HOME_ED
from app.impact_metrics import get_impact_comparison
from app.logic import get_breach_summary, get_patients_list, get_recommendations, get_status, get_utilization
from app.lwbs import get_lwbs_stats
from app.network import get_all_facilities_summary, get_hospital
from app.patients import create_patient, run_arrival_tick
from app.rate_limit import check_rate_limit
from app.relocation import get_active_destinations, relocate_patients
from app.reserve import get_float_pool, get_overflow_pool
from app.schemas import AmbulanceRequest, AssistantRequest, CheckInRequest
from app.scorecard import get_scorecard, note as note_scorecard
from app.seed import seed, seed_if_empty
from app.session_summary import build_session_summary
from app import surge

app = FastAPI(title="WayPoint Allocations")


@app.on_event("startup")
def on_startup():
    """Runs once when the server starts: create tables, then seed if empty."""
    init_db()
    seed_if_empty()


@app.get("/health")
def health():
    """Simple check to confirm the server is running."""
    return {"status": "ok"}


def build_state():
    """Read the database and assemble the full /state response.

    Shared by GET /state and POST /demo/reset, so both return data in
    exactly the same shape.
    """
    conn = get_connection()

    # Process any due discharges and try to place waiting patients into
    # whatever capacity that frees up, before reading the numbers below.
    advance_state(conn)

    patients = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'waiting' THEN 1 ELSE 0 END) AS waiting,
            SUM(CASE WHEN status = 'in_bed' THEN 1 ELSE 0 END) AS in_bed,
            SUM(CASE WHEN status = 'discharged' THEN 1 ELSE 0 END) AS discharged,
            SUM(CASE WHEN status = 'relocated' THEN 1 ELSE 0 END) AS relocated,
            SUM(CASE WHEN status = 'left_lwbs' THEN 1 ELSE 0 END) AS left_lwbs
        FROM patients
        """
    ).fetchone()

    beds = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
            SUM(CASE WHEN status = 'occupied' THEN 1 ELSE 0 END) AS occupied,
            SUM(CASE WHEN status = 'cleaning' THEN 1 ELSE 0 END) AS cleaning
        FROM beds
        """
    ).fetchone()

    rooms_total = conn.execute("SELECT COUNT(*) AS total FROM rooms").fetchone()["total"]

    nurses = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
            SUM(CASE WHEN status = 'busy' THEN 1 ELSE 0 END) AS busy,
            SUM(CASE WHEN status = 'off_shift' THEN 1 ELSE 0 END) AS off_shift
        FROM nurses
        """
    ).fetchone()

    physicians = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
            SUM(CASE WHEN status = 'busy' THEN 1 ELSE 0 END) AS busy,
            SUM(CASE WHEN status = 'off_shift' THEN 1 ELSE 0 END) AS off_shift
        FROM physicians
        """
    ).fetchone()

    arrivals_raw = conn.execute("SELECT source, COUNT(*) AS n FROM patients GROUP BY source").fetchall()
    arrivals = {"ambulance": 0, "walk_in": 0}
    for row in arrivals_raw:
        key = "ambulance" if row["source"] == "ambulance" else "walk_in"
        arrivals[key] = row["n"]

    relocated_total = conn.execute("SELECT COUNT(*) AS n FROM patients WHERE status = 'relocated'").fetchone()["n"]
    arrivals["relocated"] = relocated_total

    utilization = get_utilization(conn)
    breach_summary = get_breach_summary(conn)
    status = get_status(utilization, breach_summary)
    note_status(status["level"], breach_summary)
    note_scorecard(utilization, breach_summary, status["level"])
    float_pool = get_float_pool()
    overflow_pool = get_overflow_pool()
    recommendations = get_recommendations(utilization, breach_summary, float_pool, overflow_pool)
    patients_list = get_patients_list(conn)
    lwbs = get_lwbs_stats(conn)
    impact = get_impact_comparison(conn)

    conn.close()

    return {
        "patients": dict(patients),
        "beds": dict(beds),
        "rooms": {"total": rooms_total},
        "nurses": dict(nurses),
        "physicians": dict(physicians),
        "utilization": utilization,
        "breach_summary": breach_summary,
        "status": status,
        "recommendations": recommendations,
        "arrivals": arrivals,
        "float_pool": float_pool,
        "overflow_pool": overflow_pool,
        "event_log": get_events(),
        "relocation": {"active_destinations": get_active_destinations()},
        "scorecard": get_scorecard(),
        "lwbs": lwbs,
        "patients_list": patients_list,
        "impact": impact,
    }


@app.get("/state")
def get_state():
    """Return current counts for patients, beds, rooms, nurses, and physicians."""
    return build_state()


@app.post("/demo/reset")
def demo_reset():
    """Wipe the database and reseed the original synthetic starting scenario.

    Useful for re-running your demo from a clean, known state.
    """
    conn = get_connection()
    seed(conn)
    conn.close()
    return build_state()


@app.post("/patient/check-in")
def patient_check_in(payload: CheckInRequest = CheckInRequest()):
    """Simulated kiosk: a walk-in patient checks themselves in.

    If a bed, a nurse, and a physician are all free, they're placed
    immediately; otherwise they're added with status 'waiting' until
    capacity opens up. Leave the request body as {} to auto-generate a
    synthetic name, tier, and matching injury.
    """
    return create_patient("walk-in", name=payload.name, acuity=payload.acuity, injury=payload.injury)


@app.post("/ambulance/incoming")
def ambulance_incoming(payload: AmbulanceRequest = AmbulanceRequest()):
    """Simulated radio call: a paramedic reports a patient inbound by ambulance.

    Same placement rule as check-in, but skews toward more severe (lower)
    tiers, since ambulance patients are typically sicker than walk-ins.
    """
    return create_patient("ambulance", name=payload.name, acuity=payload.acuity, injury=payload.injury)


@app.post("/surge/start")
def surge_start():
    """Start a surge on the server: fixes its total-arrivals budget for
    this run and begins the ramp-up/peak/ramp-down profile. Returns
    started=False (a no-op) if a surge is already running, so this can
    never spin up a second generator."""
    started = surge.start_surge()
    return {"started": started, **surge.get_state()}


@app.post("/surge/stop")
def surge_stop():
    """Cancel the current surge immediately — same effect as it running
    out its duration/arrivals budget on its own."""
    surge.stop_surge()
    return surge.get_state()


@app.post("/simulate/tick")
def simulate_tick():
    """One tick of a running surge: however many patients the ramp profile
    says arrive at once (see app/surge.py), randomly split between
    ambulance and walk-in. Called once per second while the dashboard's
    'Run Surge' toggle is on. If no surge is active, or this one has hit
    its duration or arrivals cap, this creates nobody and reports the
    surge as no longer active so the dashboard can stop polling."""
    count = surge.tick_arrival_count()
    created = run_arrival_tick(count) if count > 0 else []
    return {"created": len(created), "patients": created, "surge_active": surge.is_active()}


@app.post("/recommendations/apply/{rung}")
def apply_recommendation(rung: int):
    """Actually perform the capped action for one rung of the ladder (2 or
    3) — pulling the exact quantities the current recommendation already
    computed, so this never asks for more than get_recommendations() itself
    said was available."""
    conn = get_connection()
    advance_state(conn)

    utilization = get_utilization(conn)
    breach_summary = get_breach_summary(conn)
    float_pool = get_float_pool()
    overflow_pool = get_overflow_pool()
    recommendations = get_recommendations(utilization, breach_summary, float_pool, overflow_pool)

    target = next((r for r in recommendations if r["rung"] == rung), None)
    if not target or not target.get("quantities"):
        conn.close()
        raise HTTPException(status_code=400, detail="Nothing to apply for this rung right now")

    if rung == 2:
        nurses_n, physicians_n, rooms_n = apply_float_pool(conn, target["quantities"])
        parts = []
        if nurses_n:
            parts.append(f"{nurses_n} nurse{'s' if nurses_n != 1 else ''}")
        if physicians_n:
            parts.append(f"{physicians_n} physician{'s' if physicians_n != 1 else ''}")
        if rooms_n:
            parts.append(f"{rooms_n} room{'s' if rooms_n != 1 else ''}")
        if parts:
            log_event(f"Applied: pulled {', '.join(parts)} from the hospital float pool")
    elif rung == 3:
        beds_n = apply_overflow_beds(conn, target["quantities"])
        if beds_n:
            log_event(f"Applied: opened {beds_n} overflow bed{'s' if beds_n != 1 else ''}")
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="This rung isn't directly applicable — try relocation instead")

    conn.close()
    return build_state()


@app.post("/recommendations/apply-all")
def apply_all_recommendations():
    """Walk the ladder and apply every currently-actionable rung (2, then
    3), re-checking the recommendation between each since applying one may
    change or remove the need for the next."""
    conn = get_connection()

    for rung in (2, 3):
        advance_state(conn)
        utilization = get_utilization(conn)
        breach_summary = get_breach_summary(conn)
        float_pool = get_float_pool()
        overflow_pool = get_overflow_pool()
        recommendations = get_recommendations(utilization, breach_summary, float_pool, overflow_pool)
        target = next((r for r in recommendations if r["rung"] == rung and r.get("quantities")), None)
        if not target:
            continue

        if rung == 2:
            nurses_n, physicians_n, rooms_n = apply_float_pool(conn, target["quantities"])
            parts = []
            if nurses_n:
                parts.append(f"{nurses_n} nurse{'s' if nurses_n != 1 else ''}")
            if physicians_n:
                parts.append(f"{physicians_n} physician{'s' if physicians_n != 1 else ''}")
            if rooms_n:
                parts.append(f"{rooms_n} room{'s' if rooms_n != 1 else ''}")
            if parts:
                log_event(f"Applied: pulled {', '.join(parts)} from the hospital float pool")
        elif rung == 3:
            beds_n = apply_overflow_beds(conn, target["quantities"])
            if beds_n:
                log_event(f"Applied: opened {beds_n} overflow bed{'s' if beds_n != 1 else ''}")

    conn.close()
    return build_state()


@app.post("/relocation/apply")
def apply_relocation():
    """User-approved relocation (rung 4): move up to a small batch of
    eligible waiting patients (never Tier 1) to the best available nearby
    facility. See app/relocation.py for the exact rules."""
    conn = get_connection()
    advance_state(conn)
    count = relocate_patients(conn)
    if count:
        log_event(f"Applied: relocated {count} patient{'s' if count != 1 else ''} to nearby facilities")
    advance_state(conn)  # let the freed-up queue positions backfill immediately
    conn.close()
    return build_state()


@app.get("/api/session-summary")
def api_session_summary():
    """Structured snapshot of the whole current session — one shared
    source of truth for both the situation report and the assistant, so
    neither can ever show numbers that disagree with the dashboard or
    with each other. See app/session_summary.py."""
    conn = get_connection()
    advance_state(conn)
    utilization = get_utilization(conn)
    breach_summary = get_breach_summary(conn)
    status = get_status(utilization, breach_summary)
    summary = build_session_summary(conn, utilization, breach_summary, status)
    conn.close()
    return summary


def _client_ip(request: Request):
    """Best-effort real client IP for rate limiting. Render (and most
    hosts) put the original visitor's address first in X-Forwarded-For;
    request.client.host alone would just be the proxy's address."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/assistant")
def api_assistant(payload: AssistantRequest, request: Request):
    """Ask the WayPoint assistant a question about the current simulated
    state. Never performs an action itself, never gives medical advice,
    and always answers from the same shared session summary and live
    state the dashboard itself uses — see app/assistant.py.

    Rate-limited per IP (see app/rate_limit.py) since this is the one
    endpoint in the app that costs real money. A limited or failed
    request still returns 200 with a friendly `reply`, so the frontend
    never needs special-case error handling."""
    allowed, reason = check_rate_limit(_client_ip(request))
    if not allowed:
        return {"reply": reason}

    conn = get_connection()
    advance_state(conn)
    utilization = get_utilization(conn)
    breach_summary = get_breach_summary(conn)
    status = get_status(utilization, breach_summary)
    float_pool = get_float_pool()
    overflow_pool = get_overflow_pool()
    recommendations = get_recommendations(utilization, breach_summary, float_pool, overflow_pool)
    session_summary = build_session_summary(conn, utilization, breach_summary, status)
    conn.close()

    history = [turn.model_dump() for turn in payload.history]
    reply = get_assistant_reply(
        payload.message, history, utilization, breach_summary, status, recommendations, session_summary
    )
    return {"reply": reply}


@app.get("/api/baseline-snapshot")
def api_baseline_snapshot():
    """Aggregate counts for the shadow baseline simulation (Step 4) — the
    parallel copy of the home ED that never gets float pool, overflow, or
    relocation help. Provisional/debug shape; Step 5 replaces this with a
    full comparison endpoint alongside the real ED's own numbers."""
    return baseline_simulation.get_snapshot()


@app.get("/hospitals")
def list_hospitals():
    """Summary of every nearby facility (hospitals and urgent care sites):
    status, estimated wait, travel time, distance, and capabilities.

    All of this is simulated data, and only capacity/wait information is
    shared here — never patient-level data.
    """
    return {
        "home": {"lat": HOME_ED["lat"], "lng": HOME_ED["lng"]},
        "facilities": get_all_facilities_summary(),
    }


@app.get("/hospitals/{hospital_id}/state")
def hospital_state(hospital_id: str):
    """Same response shape as the home ED's GET /state, for one of the two
    full simulated nearby hospitals (not the urgent care sites, which only
    have summary data via GET /hospitals)."""
    hospital = get_hospital(hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="Hospital not found")
    return hospital.get_state()


@app.get("/")
def dashboard():
    """Serve the dashboard's HTML page at the homepage."""
    return FileResponse("static/index.html")


# Makes everything in static/ (CSS, JS, images) available at /static/...
app.mount("/static", StaticFiles(directory="static"), name="static")
