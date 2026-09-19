"""Shared SQLite store for incoming ambulance calls.

Streamlit's `session_state` is scoped to one browser tab, so it can't be used
to hand data from a dispatcher's device to a charge nurse's dashboard on
another device. A small SQLite file on disk gives every session — regardless
of who opens the app or from where — the same source of truth. Pages poll it
via `st.fragment(run_every=...)` so updates show up without a manual refresh.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "ed_data.db"

STATUSES = ["Inbound", "Arrived", "Cancelled"]


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS incoming_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                eta_minutes INTEGER NOT NULL,
                age INTEGER,
                sex TEXT,
                chief_complaint TEXT,
                mechanism TEXT,
                heart_rate REAL,
                resp_rate REAL,
                sbp REAL,
                spo2 REAL,
                temp_c REAL,
                pain_score INTEGER,
                admission_prob REAL,
                acuity INTEGER,
                caller_notes TEXT,
                logged_by TEXT,
                status TEXT NOT NULL DEFAULT 'Inbound',
                assigned_room TEXT,
                updated_at TEXT
            )
            """
        )


def add_call(data: dict) -> int:
    now = datetime.now().isoformat()
    fields = [
        "created_at", "eta_minutes", "age", "sex", "chief_complaint", "mechanism",
        "heart_rate", "resp_rate", "sbp", "spo2", "temp_c", "pain_score",
        "admission_prob", "acuity", "caller_notes", "logged_by", "status", "updated_at",
    ]
    values = {**data, "created_at": now, "status": "Inbound", "updated_at": now}
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO incoming_calls ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
            [values.get(f) for f in fields],
        )
        return cur.lastrowid


def get_calls(status: str | None = None) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM incoming_calls WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM incoming_calls ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_status(call_id: int, status: str, assigned_room: str | None = None):
    with _conn() as conn:
        conn.execute(
            "UPDATE incoming_calls SET status = ?, assigned_room = COALESCE(?, assigned_room), updated_at = ? WHERE id = ?",
            (status, assigned_room, datetime.now().isoformat(), call_id),
        )


def eta_now(call: dict) -> int:
    """Minutes remaining until estimated arrival (can go negative if overdue)."""
    created = datetime.fromisoformat(call["created_at"])
    target = created + timedelta(minutes=call["eta_minutes"])
    remaining = (target - datetime.now()).total_seconds() / 60
    return round(remaining)
