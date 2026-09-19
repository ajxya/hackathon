"""EDFlow left-without-being-seen (LWBS) — low-tier patients who wait far
beyond their target eventually leave rather than continue waiting
indefinitely. A simple, rules-based realism feature: only Tiers 4-5 are
eligible (more severe tiers are assumed to stay), and only once their wait
is at least double their tier's target.
"""

from datetime import datetime

from app.config import SIM_MINUTES_PER_REAL_SECOND, TIER_TARGET_MINUTES

LWBS_MULTIPLIER = 2  # leaves once wait exceeds 2x their tier's target


def process_lwbs(conn):
    """Mark any eligible long-waiting patient as having left. Returns how
    many left during this pass."""
    now = datetime.now()
    rows = conn.execute(
        "SELECT id, acuity, arrival_time FROM patients WHERE status = 'waiting' AND acuity IN (4, 5)"
    ).fetchall()

    left_count = 0
    for row in rows:
        elapsed_seconds = (now - datetime.fromisoformat(row["arrival_time"])).total_seconds()
        wait_minutes = elapsed_seconds * SIM_MINUTES_PER_REAL_SECOND
        target = TIER_TARGET_MINUTES.get(row["acuity"], 0)
        if target and wait_minutes > target * LWBS_MULTIPLIER:
            conn.execute("UPDATE patients SET status = 'left_lwbs' WHERE id = ?", (row["id"],))
            left_count += 1

    if left_count:
        conn.commit()
    return left_count


def get_lwbs_stats(conn):
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'left_lwbs' THEN 1 ELSE 0 END) AS left_count,
            SUM(CASE WHEN acuity IN (4, 5) THEN 1 ELSE 0 END) AS eligible_total
        FROM patients
        """
    ).fetchone()
    left_count = row["left_count"] or 0
    eligible_total = row["eligible_total"] or 0
    rate_pct = round(left_count / eligible_total * 100, 1) if eligible_total else 0.0
    return {"left_count": left_count, "eligible_total": eligible_total, "rate_pct": rate_pct}
