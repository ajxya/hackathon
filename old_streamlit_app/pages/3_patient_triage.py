import plotly.graph_objects as go
import streamlit as st

from utils.db import get_calls, init_db, update_status
from utils.mock_data import ACUITY_COLORS
from utils.model import estimate_acuity, predict_admission
from utils.style import inject_base_css

inject_base_css()
init_db()

st.title("🩺 Patient Triage & Risk")
st.caption(
    "Enter incoming patient vitals to get an ML-driven admission probability and "
    "an estimated acuity level. Model is trained on synthetic data for demo purposes."
)

COMPLAINTS = ["Chest pain", "Shortness of breath", "Abdominal pain", "Trauma/injury",
              "Fever/infection", "Neuro (stroke-like)", "Laceration", "Other"]

arrived_calls = get_calls(status="Arrived")
prefill = {}
selected_call_id = None
if arrived_calls:
    options = {"— manual entry —": None}
    options.update({
        f"#{c['id']} · {c['chief_complaint']} · {c['age']}yo (called in by {c['logged_by']})": c["id"]
        for c in arrived_calls
    })
    choice = st.selectbox("Pull vitals from an arrived ambulance patient", list(options.keys()))
    selected_call_id = options[choice]
    if selected_call_id is not None:
        prefill = next(c for c in arrived_calls if c["id"] == selected_call_id)
        st.caption("Vitals below pre-filled from the EMS call log — adjust if the patient's condition has changed.")

widget_suffix = selected_call_id or "manual"

left, right = st.columns([1, 1.3], gap="large")

with left:
    st.subheader("Patient Vitals")
    with st.form("triage_form"):
        c1, c2 = st.columns(2)
        age = c1.number_input("Age (years)", min_value=0, max_value=110,
                               value=int(prefill.get("age", 54)), key=f"age_{widget_suffix}")
        pain_score = c2.slider("Pain score (0–10)", 0, 10,
                                int(prefill.get("pain_score", 4)), key=f"pain_{widget_suffix}")

        heart_rate = c1.number_input("Heart rate (bpm)", min_value=30, max_value=220,
                                      value=int(prefill.get("heart_rate", 92)), key=f"hr_{widget_suffix}")
        resp_rate = c2.number_input("Resp. rate (breaths/min)", min_value=6, max_value=50,
                                     value=int(prefill.get("resp_rate", 19)), key=f"rr_{widget_suffix}")

        sbp = c1.number_input("Systolic BP (mmHg)", min_value=50, max_value=250,
                               value=int(prefill.get("sbp", 128)), key=f"sbp_{widget_suffix}")
        spo2 = c2.number_input("SpO2 (%)", min_value=50, max_value=100,
                                value=int(prefill.get("spo2", 96)), key=f"spo2_{widget_suffix}")

        temp_c = c1.number_input("Temperature (°C)", min_value=33.0, max_value=42.0,
                                  value=float(prefill.get("temp_c", 37.1)), step=0.1, key=f"temp_{widget_suffix}")
        default_complaint = prefill.get("chief_complaint", "Chest pain")
        chief_complaint = c2.selectbox(
            "Chief complaint", COMPLAINTS,
            index=COMPLAINTS.index(default_complaint) if default_complaint in COMPLAINTS else 0,
            key=f"cc_{widget_suffix}",
        )

        submitted = st.form_submit_button("Run Prediction", use_container_width=True, type="primary")

with right:
    st.subheader("Model Output")
    if not submitted:
        st.info("Fill in the vitals on the left and click **Run Prediction** to see the risk assessment.")
    else:
        vitals = {
            "age": age, "heart_rate": heart_rate, "resp_rate": resp_rate,
            "sbp": sbp, "spo2": spo2, "temp_c": temp_c, "pain_score": pain_score,
        }
        prob = predict_admission(vitals)
        acuity = estimate_acuity(vitals, prob)
        acuity_labels = {
            1: "ESI 1 — Resuscitation", 2: "ESI 2 — Emergent", 3: "ESI 3 — Urgent",
            4: "ESI 4 — Less Urgent", 5: "ESI 5 — Non-Urgent",
        }

        m1, m2 = st.columns(2)
        m1.metric("Admission Probability", f"{prob*100:.1f}%")
        m2.metric("Estimated Acuity", acuity_labels[acuity])

        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=prob * 100,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": ACUITY_COLORS[acuity]},
                "steps": [
                    {"range": [0, 25], "color": "#1E8E5A22"},
                    {"range": [25, 50], "color": "#3B7DD822"},
                    {"range": [50, 75], "color": "#B8860B22"},
                    {"range": [75, 100], "color": "#B3261E22"},
                ],
            },
        ))
        fig.update_layout(height=280, margin=dict(l=20, r=20, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        if acuity <= 2:
            st.error(f"⚠️ High acuity — recommend immediate provider evaluation. Complaint: {chief_complaint}.")
        elif acuity == 3:
            st.warning(f"Moderate acuity — prioritize in queue. Complaint: {chief_complaint}.")
        else:
            st.success(f"Lower acuity — routine queue appropriate. Complaint: {chief_complaint}.")

        with st.expander("Feature inputs sent to model"):
            st.json(vitals)

        if selected_call_id is not None:
            if st.button("Mark EMS record as triaged (remove from arrived queue)"):
                update_status(selected_call_id, "Triaged")
                st.rerun()

st.divider()
st.caption(
    "⚠️ Demo only: predictions come from a logistic regression trained on synthetic, "
    "not real patient, data. Not for clinical decision-making."
)
