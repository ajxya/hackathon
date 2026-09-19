import plotly.graph_objects as go
import streamlit as st

from utils.mock_data import STATUS_COLORS, nurse_roster
from utils.style import inject_base_css, render_alert_banner

inject_base_css()

st.title("🧑‍⚕️ Staff & Resource Allocator")
st.caption("Interactive workspace for nurse-to-patient load balancing across zones.")

if "roster" not in st.session_state:
    st.session_state.roster = nurse_roster()

roster = st.session_state.roster

zone_target = {
    "Resus": 2, "Acute": 4, "Fast Track": 5, "Peds": 4,
}

overloaded = roster[roster["load_pct"] > 100]
if len(overloaded):
    render_alert_banner("serious", f"{len(overloaded)} nurse(s) exceeding target ratio — rebalance recommended.")
else:
    render_alert_banner("good", "All nurses within target patient-to-nurse ratio.")

st.divider()

col1, col2 = st.columns([1.3, 1], gap="large")

with col1:
    st.subheader("Current Assignments")
    edited = st.data_editor(
        roster,
        column_config={
            "nurse": st.column_config.TextColumn("Nurse", disabled=True),
            "zone": st.column_config.SelectboxColumn("Zone", options=list(zone_target.keys())),
            "patients_assigned": st.column_config.NumberColumn("Patients", min_value=0, max_value=10, step=1),
            "max_ratio": st.column_config.NumberColumn("Target Ratio", disabled=True),
            "load_pct": st.column_config.ProgressColumn("Load %", min_value=0, max_value=150, format="%.0f%%"),
        },
        hide_index=True,
        use_container_width=True,
        key="roster_editor",
    )
    edited["max_ratio"] = edited["zone"].map(lambda z: {"Resus": 2, "Acute": 4, "Fast Track": 5, "Peds": 4}[z])
    edited["load_pct"] = (edited["patients_assigned"] / edited["max_ratio"] * 100).round(0)
    st.session_state.roster = edited

    if st.button("↻ Auto-balance load across zones", use_container_width=True):
        df = st.session_state.roster.copy()
        for zone in df["zone"].unique():
            mask = df["zone"] == zone
            total_patients = df.loc[mask, "patients_assigned"].sum()
            n_nurses = mask.sum()
            base, remainder = divmod(int(total_patients), n_nurses)
            new_loads = [base + (1 if i < remainder else 0) for i in range(n_nurses)]
            df.loc[mask, "patients_assigned"] = new_loads
        df["load_pct"] = (df["patients_assigned"] / df["max_ratio"] * 100).round(0)
        st.session_state.roster = df
        st.rerun()

with col2:
    st.subheader("Load by Zone")
    zone_summary = st.session_state.roster.groupby("zone").agg(
        nurses=("nurse", "count"),
        patients=("patients_assigned", "sum"),
    ).reset_index()
    zone_summary["avg_ratio"] = (zone_summary["patients"] / zone_summary["nurses"]).round(2)
    zone_summary["target"] = zone_summary["zone"].map(zone_target)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=zone_summary["zone"], y=zone_summary["avg_ratio"],
        marker_color="#3B7DD8", name="Actual avg. patients/nurse",
    ))
    fig.add_trace(go.Scatter(
        x=zone_summary["zone"], y=zone_summary["target"],
        mode="markers", marker=dict(color="#B3261E", size=14, symbol="diamond"),
        name="Target ratio",
    ))
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="Patients / nurse",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Shift Coverage (next 8h)")
    st.dataframe(
        {
            "Shift block": ["Now–2h", "2–4h", "4–6h", "6–8h"],
            "Nurses scheduled": [14, 13, 16, 12],
            "Projected census": [66, 70, 61, 58],
        },
        hide_index=True,
        use_container_width=True,
    )
    st.caption("Coverage gap projected in the 6–8h block if census holds — consider calling in relief staff.")

st.divider()
st.caption("Drag values in the table to simulate reassignments, or use auto-balance to level load within each zone.")
