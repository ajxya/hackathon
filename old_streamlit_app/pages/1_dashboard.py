import plotly.graph_objects as go
import streamlit as st

from utils.db import eta_now, get_calls, init_db
from utils.mock_data import alerts, current_metrics, hourly_volume, wait_time_trend
from utils.style import inject_base_css, render_alert_banner

inject_base_css()
init_db()

st.title("🏥 ED Overview")
st.caption("High-level snapshot of current emergency department load and system health.")

for a in alerts():
    render_alert_banner(a["level"], a["text"])


@st.fragment(run_every="5s")
def inbound_ambulance_banner():
    calls = get_calls(status="Inbound")
    if not calls:
        return
    critical = [c for c in calls if c["acuity"] <= 2]
    next_eta = min(eta_now(c) for c in calls)
    level = "critical" if critical else "warning"
    text = (
        f"{len(calls)} ambulance(s) inbound, next arriving in ~{max(next_eta, 0)} min"
        + (f" — {len(critical)} high-acuity" if critical else "")
        + ". See Incoming Ambulance for details."
    )
    render_alert_banner(level, text)


inbound_ambulance_banner()

st.divider()

metrics = current_metrics()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Current Census", f"{metrics['census']} / {metrics['capacity']}",
          help="Patients currently in the ED vs. total treatment-space capacity")
c2.metric("Avg. Wait Time", f"{metrics['avg_wait_min']} min", delta="+6.2 vs. yesterday", delta_color="inverse")
c3.metric("Boarding Patients", metrics["boarding_count"], delta="awaiting inpatient bed", delta_color="off")
c4.metric("Staffed Nurses", metrics["staffed_nurses"])
c5.metric("Left w/o Being Seen", f"{metrics['left_without_being_seen_pct']}%", delta="-0.3 pts", delta_color="normal")

st.divider()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Patient Arrivals (last 24h)")
    df_vol = hourly_volume()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_vol["timestamp"], y=df_vol["arrivals"],
        marker_color="#3B7DD8", name="Arrivals",
    ))
    fig.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title=None, yaxis_title="Patients / hour",
        showlegend=False, plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Median Wait Time (last 24h)")
    df_wait = wait_time_trend()
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(
        x=df_wait["timestamp"], y=df_wait["wait_minutes"],
        mode="lines", line=dict(color="#C9622A", width=2),
        fill="tozeroy", fillcolor="rgba(201,98,42,0.12)", name="Wait (min)",
    ))
    fig2.add_hline(y=45, line_dash="dot", line_color="#B8860B",
                    annotation_text="target: 45 min", annotation_position="top left")
    fig2.update_layout(
        height=340, margin=dict(l=10, r=10, t=10, b=10),
        xaxis_title=None, yaxis_title="Minutes",
        showlegend=False, plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig2, use_container_width=True)

st.divider()
st.caption(
    "All figures on this page are synthetic demo data regenerated each session. "
    "In production this page would ingest live feeds from the ADT/EHR system."
)
