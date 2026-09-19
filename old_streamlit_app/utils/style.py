"""Small shared CSS + banner helpers so all pages look consistent."""

import streamlit as st

from utils.mock_data import STATUS_COLORS

BASE_CSS = """
<style>
[data-testid="stMetric"] {
    background: var(--secondary-background-color);
    border: 1px solid rgba(128,128,128,0.25);
    border-radius: 10px;
    padding: 14px 16px 8px 16px;
}
.ed-banner {
    border-radius: 8px;
    padding: 10px 14px;
    margin-bottom: 8px;
    font-size: 0.92rem;
    border-left: 5px solid transparent;
}
</style>
"""


def inject_base_css():
    st.markdown(BASE_CSS, unsafe_allow_html=True)


def render_alert_banner(level: str, text: str):
    color = STATUS_COLORS.get(level, STATUS_COLORS["good"])
    icon = {"good": "✅", "warning": "⚠️", "serious": "🟠", "critical": "🔴"}.get(level, "ℹ️")
    st.markdown(
        f"""<div class="ed-banner" style="border-left-color:{color}; background:{color}14;">
        <strong>{icon} {level.upper()}</strong> — {text}
        </div>""",
        unsafe_allow_html=True,
    )
