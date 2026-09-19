"""Mock ML layer for the ED triage page.

For the hackathon MVP we synthesize a plausible training set (vitals -> admit
label) using clinically-motivated rules plus noise, then fit a real
scikit-learn LogisticRegression on it. This is NOT a validated clinical model —
it exists to demonstrate the interaction pattern (vitals in, risk score out)
that a real model trained on hospital data would slot into later.
"""

import numpy as np
import pandas as pd
import streamlit as st
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

FEATURES = ["age", "heart_rate", "resp_rate", "sbp", "spo2", "temp_c", "pain_score"]


def _synthesize_training_data(n: int = 4000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    age = rng.gamma(shape=4.0, scale=12, size=n).clip(0, 100)
    heart_rate = rng.normal(85, 18, n).clip(40, 180)
    resp_rate = rng.normal(18, 5, n).clip(8, 45)
    sbp = rng.normal(122, 22, n).clip(70, 220)
    spo2 = rng.normal(96.5, 3, n).clip(70, 100)
    temp_c = rng.normal(37.0, 0.8, n).clip(34, 41)
    pain_score = rng.integers(0, 11, n)

    # Clinically-flavored risk score used only to LABEL synthetic training rows.
    risk = (
        0.035 * (age - 50)
        + 0.05 * (heart_rate - 90)
        + 0.09 * (resp_rate - 20)
        - 0.06 * (sbp - 110)
        - 0.28 * (spo2 - 94)
        + 0.7 * (temp_c - 38.5).clip(min=0) * 3
        + 0.15 * pain_score
        + rng.normal(0, 3, n)
    )
    prob = 1 / (1 + np.exp(-(risk - 2) / 4))
    admitted = (rng.uniform(size=n) < prob).astype(int)

    return pd.DataFrame({
        "age": age, "heart_rate": heart_rate, "resp_rate": resp_rate,
        "sbp": sbp, "spo2": spo2, "temp_c": temp_c, "pain_score": pain_score,
        "admitted": admitted,
    })


@st.cache_resource(show_spinner=False)
def get_model():
    """Train (once, cached) a small logistic regression on synthetic data."""
    df = _synthesize_training_data()
    X = df[FEATURES].values
    y = df["admitted"].values
    scaler = StandardScaler().fit(X)
    clf = LogisticRegression(max_iter=1000).fit(scaler.transform(X), y)
    return clf, scaler


def predict_admission(vitals: dict) -> float:
    """Return admission probability (0-1) for a single patient's vitals."""
    clf, scaler = get_model()
    x = np.array([[vitals[f] for f in FEATURES]])
    return float(clf.predict_proba(scaler.transform(x))[0, 1])


def estimate_acuity(vitals: dict, admission_prob: float) -> int:
    """Rough ESI-style acuity (1=most urgent, 5=least) from vitals + risk.

    Not a validated ESI algorithm — a simplified stand-in that combines a few
    hard physiologic thresholds with the model's admission probability.
    """
    if vitals["spo2"] < 90 or vitals["sbp"] < 80 or vitals["resp_rate"] > 32:
        return 1
    if admission_prob > 0.75 or vitals["heart_rate"] > 140:
        return 2
    if admission_prob > 0.5:
        return 3
    if admission_prob > 0.25:
        return 4
    return 5
