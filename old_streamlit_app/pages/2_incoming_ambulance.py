import streamlit as st

from utils.db import add_call, eta_now, get_calls, init_db, update_status
from utils.model import estimate_acuity, predict_admission
from utils.style import inject_base_css

inject_base_css()
init_db()

st.title("🚨 Incoming Ambulance")
st.caption(
    "Log a call the moment it comes in. It appears on the Dashboard and in this queue "
    "immediately for every device viewing this app — no refresh needed."
)

left, right = st.columns([1, 1.3], gap="large")

with left:
    st.subheader("Log New Call")
    with st.form("ambulance_form", clear_on_submit=True):
        eta_minutes = st.slider("ETA (minutes)", 1, 60, 12)
        c1, c2 = st.columns(2)
        age = c1.number_input("Age (years)", min_value=0, max_value=110, value=60)
        sex = c2.selectbox("Sex", ["Male", "Female", "Unknown"])

        chief_complaint = st.selectbox(
            "Chief complaint",
            ["Chest pain", "Shortness of breath", "Abdominal pain", "Trauma/injury",
             "Fever/infection", "Neuro (stroke-like)", "Cardiac arrest", "Other"],
        )
        mechanism = st.text_input("Mechanism / history (optional)", placeholder="e.g. MVC, fall from ladder")

        st.markdown("**Vitals reported by EMS**")
        c3, c4 = st.columns(2)
        heart_rate = c3.number_input("Heart rate (bpm)", min_value=30, max_value=220, value=95)
        resp_rate = c4.number_input("Resp. rate (breaths/min)", min_value=6, max_value=50, value=20)
        sbp = c3.number_input("Systolic BP (mmHg)", min_value=50, max_value=250, value=120)
        spo2 = c4.number_input("SpO2 (%)", min_value=50, max_value=100, value=95)
        temp_c = c3.number_input("Temperature (°C)", min_value=33.0, max_value=42.0, value=37.0, step=0.1)
        pain_score = c4.slider("Pain score (0–10)", 0, 10, 5)

        caller_notes = st.text_area("Additional notes for receiving team", placeholder="Anything else dispatch relayed")
        logged_by = st.text_input("Logged by (your name/role)", placeholder="e.g. Dispatcher — J. Alam")

        submitted = st.form_submit_button("📞 Log Incoming Patient", use_container_width=True, type="primary")

    if submitted:
        vitals = {
            "age": age, "heart_rate": heart_rate, "resp_rate": resp_rate,
            "sbp": sbp, "spo2": spo2, "temp_c": temp_c, "pain_score": pain_score,
        }
        prob = predict_admission(vitals)
        acuity = estimate_acuity(vitals, prob)
        call_id = add_call({
            "eta_minutes": eta_minutes, "age": age, "sex": sex,
            "chief_complaint": chief_complaint, "mechanism": mechanism,
            "heart_rate": heart_rate, "resp_rate": resp_rate, "sbp": sbp,
            "spo2": spo2, "temp_c": temp_c, "pain_score": pain_score,
            "admission_prob": prob, "acuity": acuity,
            "caller_notes": caller_notes, "logged_by": logged_by or "Unspecified",
        })
        st.success(f"Call #{call_id} logged — ETA {eta_minutes} min. Now visible on Dashboard and in the queue.")
        st.rerun()

with right:
    st.subheader("Live Inbound Queue")

    @st.fragment(run_every="5s")
    def inbound_queue():
        calls = get_calls(status="Inbound")
        if not calls:
            st.info("No ambulances currently inbound.")
            return

        for call in calls:
            minutes_left = eta_now(call)
            eta_label = f"{minutes_left} min" if minutes_left >= 0 else f"{-minutes_left} min overdue"
            acuity_flag = "🔴" if call["acuity"] <= 2 else ("🟠" if call["acuity"] == 3 else "🟢")

            with st.container(border=True):
                h1, h2 = st.columns([3, 1])
                h1.markdown(f"**{acuity_flag} #{call['id']} · {call['chief_complaint']}** — {call['age']}yo {call['sex']}")
                h2.markdown(f"**ETA {eta_label}**")

                st.caption(
                    f"HR {call['heart_rate']:.0f} · RR {call['resp_rate']:.0f} · SBP {call['sbp']:.0f} · "
                    f"SpO2 {call['spo2']:.0f}% · Temp {call['temp_c']:.1f}°C · Pain {call['pain_score']}/10  \n"
                    f"Admission risk **{call['admission_prob']*100:.0f}%** · Est. acuity **ESI {call['acuity']}** · "
                    f"Logged by {call['logged_by']}"
                )
                if call["mechanism"]:
                    st.caption(f"Mechanism/history: {call['mechanism']}")
                if call["caller_notes"]:
                    st.caption(f"Notes: {call['caller_notes']}")

                b1, b2 = st.columns(2)
                if b1.button("✅ Mark Arrived", key=f"arrive_{call['id']}", use_container_width=True):
                    update_status(call["id"], "Arrived")
                    st.rerun(scope="fragment")
                if b2.button("✖ Cancel", key=f"cancel_{call['id']}", use_container_width=True):
                    update_status(call["id"], "Cancelled")
                    st.rerun(scope="fragment")

    inbound_queue()

st.divider()
with st.expander("Recently arrived / cancelled calls"):
    past = [c for c in get_calls() if c["status"] != "Inbound"][:10]
    if not past:
        st.caption("Nothing yet.")
    for call in past:
        st.caption(
            f"#{call['id']} · {call['chief_complaint']} · {call['age']}yo — **{call['status']}** "
            f"(logged {call['created_at'][:16].replace('T', ' ')})"
        )

st.caption(
    "This queue is stored in a shared SQLite file (`ed_data.db`), so it's visible from any browser "
    "hitting this app — a dispatcher on one machine and a charge nurse on another see the same list."
)
