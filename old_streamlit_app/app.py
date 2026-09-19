"""ED Resource Allocator — entry point.

Defines the four-page sidebar navigation:
Dashboard / Patient Triage & Risk / Staff Allocator / Bed Flow Tracker.
Run with:  streamlit run app.py
"""

import streamlit as st

st.set_page_config(
    page_title="ED Resource Allocator",
    page_icon="🚑",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page("pages/1_dashboard.py", title="Dashboard", icon="🏥", default=True),
    st.Page("pages/2_incoming_ambulance.py", title="Incoming Ambulance", icon="🚨"),
    st.Page("pages/3_patient_triage.py", title="Patient Triage & Risk", icon="🩺"),
    st.Page("pages/4_staff_allocator.py", title="Staff Allocator", icon="🧑‍⚕️"),
    st.Page("pages/5_bed_flow.py", title="Bed Flow Tracker", icon="🛏️"),
]

with st.sidebar:
    st.markdown("### 🚑 ED Resource Allocator")
    st.caption("Real-time triage, staffing & bed-flow intelligence")

nav = st.navigation(pages)
nav.run()

with st.sidebar:
    st.divider()
    st.caption("MVP demo · synthetic data · not for clinical use")
