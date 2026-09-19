"""EDFlow database setup — plain SQLite, no extra libraries.

Defines the tables and a function to create them if they don't exist yet.
"""

import sqlite3
from pathlib import Path

# The database file lives at the project root, next to app/ and static/.
DB_PATH = Path(__file__).resolve().parent.parent / "edflow.db"


def get_connection():
    """Open a connection to the database.

    row_factory = sqlite3.Row lets us read query results like dictionaries
    (e.g. row["name"]) instead of needing numeric indexes like row[0].

    isolation_level = None puts the connection in autocommit mode, so code
    that needs an atomic multi-statement operation (like allocation.py's
    advance_state) can explicitly BEGIN IMMEDIATE / COMMIT around it. The
    dashboard polls every 2 seconds while a surge fires requests every
    second, so two requests really can arrive at the same instant —
    busy_timeout makes one just wait briefly for the other's transaction to
    finish, instead of the two racing to assign the same bed.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db():
    """Create every table if it doesn't already exist. Safe to run every startup."""
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            room_type TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS beds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id),
            label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',  -- available | occupied | cleaning
            cleaning_until TEXT,                        -- when a 'cleaning' bed becomes available again
            created_at TEXT                             -- when this bed was stood up (grace period before it's returnable)
        );

        CREATE TABLE IF NOT EXISTS nurses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',  -- available | busy | off_shift
            max_patients INTEGER NOT NULL DEFAULT 4,
            current_patients INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'staff',      -- staff | float (pulled from the float pool)
            added_at TEXT                               -- when a float nurse was pulled in (grace period before return)
        );

        CREATE TABLE IF NOT EXISTS physicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',  -- available | busy | off_shift
            max_patients INTEGER NOT NULL DEFAULT 6,
            current_patients INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'staff',      -- staff | float (pulled from the float pool)
            added_at TEXT                               -- when a float physician was pulled in (grace period before return)
        );

        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            arrival_time TEXT NOT NULL,
            source TEXT NOT NULL,                      -- walk-in | ambulance
            acuity INTEGER NOT NULL,                    -- priority tier: 1 (most severe) to 5 (least severe)
            injury TEXT,                                 -- the specific injury/condition assigned to this patient
            los_seconds REAL,                            -- length of stay once admitted, drawn once at arrival
            status TEXT NOT NULL DEFAULT 'waiting',      -- waiting | in_bed | discharged
            bed_id INTEGER REFERENCES beds(id),
            nurse_id INTEGER REFERENCES nurses(id),
            physician_id INTEGER REFERENCES physicians(id),
            bed_assigned_at TEXT,                        -- when they were placed in a bed
            discharge_due_at TEXT,                       -- when their own length of stay elapses
            left_at TEXT                                 -- when a left-without-being-seen patient actually left
        );
        """
    )
    conn.commit()
    conn.close()
