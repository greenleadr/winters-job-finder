"""Separate tracking database for application status.

Stores user-set statuses (applied, interviewing, offer, rejected) in
a dedicated DB that CI never overwrites. The main pipeline reads from
this DB to merge status overrides into the dashboard and digest.

Schema: one table mapping job_id → status + notes + timestamp.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TRACKING_PATH = Path(__file__).resolve().parent / "data" / "tracking.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tracking (
    job_id    TEXT PRIMARY KEY,
    status    TEXT NOT NULL,
    notes     TEXT NOT NULL DEFAULT '',
    updated   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    job_id    TEXT PRIMARY KEY,
    created   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL,
    field     TEXT NOT NULL,
    rating    TEXT NOT NULL,
    created   TEXT NOT NULL
);
"""

_VALID_STATUSES = {"applied", "interviewing", "offer", "rejected", "withdrawn"}


def init_tracking(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _TRACKING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def set_status(
    conn: sqlite3.Connection,
    job_id: str,
    status: str,
    notes: str = "",
) -> None:
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {sorted(_VALID_STATUSES)}")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tracking (job_id, status, notes, updated) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(job_id) DO UPDATE SET status = ?, notes = ?, updated = ?",
        (job_id, status, notes, now, status, notes, now),
    )
    conn.commit()


def remove_status(conn: sqlite3.Connection, job_id: str) -> bool:
    result = conn.execute("DELETE FROM tracking WHERE job_id = ?", (job_id,))
    conn.commit()
    return result.rowcount > 0


def get_all_overrides(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Return {job_id: {status, notes, updated}} for all tracked jobs."""
    rows = conn.execute("SELECT * FROM tracking").fetchall()
    return {
        row["job_id"]: {
            "status": row["status"],
            "notes": row["notes"],
            "updated": row["updated"],
        }
        for row in rows
    }


def get_funnel(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM tracking GROUP BY status"
    ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


def get_by_status(conn: sqlite3.Connection, status: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM tracking WHERE status = ? ORDER BY updated DESC",
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

_VALID_FIELDS = {"recommendation", "score", "match"}
_VALID_RATINGS = {"accurate", "inaccurate"}


def add_feedback(
    conn: sqlite3.Connection,
    job_id: str,
    field: str,
    rating: str,
) -> None:
    if field not in _VALID_FIELDS:
        raise ValueError(f"Invalid field '{field}'. Must be one of: {sorted(_VALID_FIELDS)}")
    if rating not in _VALID_RATINGS:
        raise ValueError(f"Invalid rating '{rating}'. Must be one of: {sorted(_VALID_RATINGS)}")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO feedback (job_id, field, rating, created) VALUES (?, ?, ?, ?)",
        (job_id, field, rating, now),
    )
    conn.commit()


def get_feedback_for_job(conn: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM feedback WHERE job_id = ? ORDER BY created DESC",
        (job_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_feedback_stats(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Return {field: {accurate: N, inaccurate: N}} aggregate stats."""
    rows = conn.execute(
        "SELECT field, rating, COUNT(*) as cnt FROM feedback GROUP BY field, rating"
    ).fetchall()
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        f = row["field"]
        if f not in stats:
            stats[f] = {"accurate": 0, "inaccurate": 0}
        stats[f][row["rating"]] = row["cnt"]
    return stats


# ---------------------------------------------------------------------------
# Favorites / Shortlist
# ---------------------------------------------------------------------------

def add_favorite(conn: sqlite3.Connection, job_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO favorites (job_id, created) VALUES (?, ?) "
        "ON CONFLICT(job_id) DO NOTHING",
        (job_id, now),
    )
    conn.commit()


def remove_favorite(conn: sqlite3.Connection, job_id: str) -> bool:
    result = conn.execute("DELETE FROM favorites WHERE job_id = ?", (job_id,))
    conn.commit()
    return result.rowcount > 0


def get_all_favorites(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT job_id FROM favorites").fetchall()
    return {row["job_id"] for row in rows}
