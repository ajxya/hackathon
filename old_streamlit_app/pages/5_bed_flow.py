import plotly.graph_objects as go
import streamlit as st

from utils.mock_data import ROOM_STATUS_COLORS, bed_grid
from utils.style import inject_base_css, render_alert_banner

inject_base_css()

st.title("🛏️ Bed Flow Tracker")
st.caption("Visual room layout, cleaning turnaround, and boarding bottlenecks.")

if "beds" not in st.session_state:
    st.session_state.beds = bed_grid()

df = st.session_state.beds

counts = df["status"].value_counts()
long_boarders = df[(df["status"] == "Boarding") & (df["minutes_in_status"] > 120)]

if len(long_boarders):
    render_alert_banner("critical", f"{len(long_boarders)} patient(s) boarding over 2 hours — inpatient bed request needed.")
else:
    render_alert_banner("good", "No boarding patients exceeding the 2-hour threshold.")

st.divider()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Available", int(counts.get("Available", 0)))
c2.metric("Occupied", int(counts.get("Occupied", 0)))
c3.metric("Boarding", int(counts.get("Boarding", 0)))
c4.metric("Cleaning", int(counts.get("Cleaning", 0)))

st.divider()

col1, col2 = st.columns([1.5, 1], gap="large")

with col1:
    st.subheader("Room Layout")

    fig = go.Figure()
    for status, color in ROOM_STATUS_COLORS.items():
        sub = df[df["status"] == status]
        fig.add_trace(go.Scatter(
            x=sub["col"], y=-sub["row"],
            mode="markers+text",
            marker=dict(size=34, color=color, symbol="square", line=dict(width=1, color="white")),
            text=sub["room"], textfont=dict(size=9, color="white"),
            name=status,
            hovertext=[f"{r} · {status} · {m} min" for r, m in zip(sub["room"], sub["minutes_in_status"])],
            hoverinfo="text",
        ))
    fig.update_layout(
        height=380, margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    if st.button("↻ Refresh room statuses", use_container_width=True):
        st.session_state.beds = bed_grid()
        st.rerun()

with col2:
    st.subheader("Boarding Bottleneck")
    board_df = df[df["status"] == "Boarding"].sort_values("minutes_in_status", ascending=False)
    if len(board_df):
        st.dataframe(
            board_df[["room", "zone", "minutes_in_status"]].rename(
                columns={"room": "Room", "zone": "Zone", "minutes_in_status": "Minutes boarding"}
            ),
            hide_index=True, use_container_width=True,
        )
    else:
        st.success("No patients currently boarding.")

    st.subheader("Cleaning Turnaround")
    clean_df = df[df["status"] == "Cleaning"].sort_values("minutes_in_status", ascending=False)
    if len(clean_df):
        st.dataframe(
            clean_df[["room", "zone", "minutes_in_status"]].rename(
                columns={"room": "Room", "zone": "Zone", "minutes_in_status": "Minutes cleaning"}
            ),
            hide_index=True, use_container_width=True,
        )
    else:
        st.info("No rooms currently in cleaning.")

st.divider()
st.caption("Room grid regenerates from synthetic data on refresh — in production this maps to live ADT bed-status feeds.")
