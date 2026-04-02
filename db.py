"""SQLite persistence for seen jobs and run history.

Stores job postings by a deterministic ID (hash of title+company+url)
so the pipeline can skip duplicates across runs.

Usage:
    python db.py              # runs a self-test with demo data
    python -m db              # same
"""

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_DB_DIR = Path(__file__).resolve().parent / "data"
_DB_PATH = _DB_DIR / "jobs.db"


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# Tracking query params to strip before dedup hashing
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "gbraid", "wbraid", "msclkid",
    "trk", "ref", "referer", "source", "si", "mc_cid", "mc_eid",
    "gh_jid", "gh_src", "lever-via", "lever-source", "lever-origin",
}


def _normalize_url(url: str) -> str:
    """Strip tracking/referral query params from a URL for consistent dedup."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=False)
        cleaned = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        new_query = urlencode(cleaned, doseq=True) if cleaned else ""
        return urlunparse(parsed._replace(query=new_query, fragment=""))
    except Exception:
        return url


def job_id(title: str, company: str, url: str) -> str:
    """Deterministic ID: SHA-256 of title|company|normalized_url (lowercased)."""
    clean_url = _normalize_url(url).lower().strip()
    raw = f"{title.lower().strip()}|{company.lower().strip()}|{clean_url}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    date_posted TEXT NOT NULL DEFAULT '',
    score       INTEGER,
    matched_skills TEXT,
    flags       TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    salary_min  INTEGER,
    salary_max  INTEGER
);

CREATE TABLE IF NOT EXISTS llm_cache (
    job_id   TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_score      ON jobs(score);
"""

_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN status TEXT NOT NULL DEFAULT 'open'",
    "ALTER TABLE jobs ADD COLUMN salary_min INTEGER",
    "ALTER TABLE jobs ADD COLUMN salary_max INTEGER",
]


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create tables if needed and return the connection."""
    conn = _connect(db_path)
    conn.executescript(_SCHEMA)
    # Run migrations (ignore if column already exists)
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def is_seen(conn: sqlite3.Connection, jid: str) -> bool:
    """Return True if a job with this ID already exists."""
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (jid,)).fetchone()
    return row is not None


def save_jobs(
    conn: sqlite3.Connection,
    jobs: list[dict[str, Any]],
) -> int:
    """Upsert *jobs* into the database.  Returns the number of new inserts."""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0

    for job in jobs:
        jid = job_id(
            job.get("title", ""),
            job.get("company", ""),
            job.get("url", ""),
        )
        score_data = job.get("_score", {})
        matched_json = json.dumps(score_data.get("matched_skills", []))
        flags_json = json.dumps(score_data.get("flags", []))
        score_val = score_data.get("score")
        salary_min = score_data.get("salary_min")
        salary_max = score_data.get("salary_max")

        existing = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (jid,)).fetchone()
        if existing:
            # Only update score if we have a real one (don't overwrite with None)
            if score_val is not None:
                conn.execute(
                    "UPDATE jobs SET last_seen = ?, score = ?, matched_skills = ?, "
                    "flags = ?, salary_min = COALESCE(?, salary_min), "
                    "salary_max = COALESCE(?, salary_max) WHERE id = ?",
                    (now, score_val, matched_json, flags_json,
                     salary_min, salary_max, jid),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET last_seen = ? WHERE id = ?",
                    (now, jid),
                )
        else:
            conn.execute(
                """INSERT INTO jobs
                   (id, title, company, location, url, description,
                    source, date_posted, score, matched_skills, flags,
                    first_seen, last_seen, salary_min, salary_max)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    jid,
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("url", ""),
                    job.get("description", ""),
                    job.get("source", ""),
                    job.get("date_posted", ""),
                    score_val,
                    matched_json,
                    flags_json,
                    now,
                    now,
                    salary_min,
                    salary_max,
                ),
            )
            inserted += 1

    conn.commit()
    return inserted


def mark_closed(
    conn: sqlite3.Connection,
    current_titles_companies: set[str],
    max_age_days: int = 7,
) -> int:
    """Mark jobs as 'closed' if their title+company combo is no longer
    in the current collection.  Uses title+company instead of URL to
    avoid false positives from Greenhouse multi-location variants.

    *current_titles_companies* should be a set of "title|company" strings
    (lowercased).

    Returns the number of jobs marked closed.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    rows = conn.execute(
        "SELECT id, title, company FROM jobs WHERE status = 'open' AND first_seen >= ?",
        (cutoff,),
    ).fetchall()

    closed = 0
    for row in rows:
        key = f"{(row['title'] or '').lower().strip()}|{(row['company'] or '').lower().strip()}"
        if key not in current_titles_companies:
            conn.execute(
                "UPDATE jobs SET status = 'closed' WHERE id = ?", (row["id"],)
            )
            closed += 1

    if closed:
        conn.commit()
    return closed


def get_open_jobs(
    conn: sqlite3.Connection,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Return open jobs first seen within the last *days* days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'open' AND first_seen >= ? "
        "ORDER BY score DESC, first_seen DESC",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_long_open_jobs(
    conn: sqlite3.Connection,
    min_days: int = 7,
    max_days: int = 30,
) -> list[dict[str, Any]]:
    """Return jobs that have been open for 7+ days — a positive signal.

    These roles haven't been filled yet, so they may be worth applying to.
    """
    now = datetime.now(timezone.utc)
    recent_cutoff = (now - timedelta(days=min_days)).isoformat()
    old_cutoff = (now - timedelta(days=max_days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'open' "
        "AND first_seen < ? AND first_seen >= ? AND (score IS NOT NULL AND score > 0) "
        "ORDER BY score DESC",
        (recent_cutoff, old_cutoff),
    ).fetchall()
    return [dict(row) for row in rows]


def get_closed_jobs(
    conn: sqlite3.Connection,
    days: int = 3,
) -> list[dict[str, Any]]:
    """Return recently closed jobs."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'closed' AND last_seen >= ? "
        "ORDER BY score DESC",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


_VALID_STATUSES = {"open", "closed", "applied", "interviewing", "rejected", "offer"}


def set_status(
    conn: sqlite3.Connection,
    jid: str,
    status: str,
) -> bool:
    """Manually set a job's application status.

    Valid statuses: open, closed, applied, interviewing, rejected, offer.
    Returns True if the job was found and updated.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {_VALID_STATUSES}")
    result = conn.execute(
        "UPDATE jobs SET status = ? WHERE id = ?", (status, jid)
    )
    conn.commit()
    return result.rowcount > 0


def get_jobs_by_status(
    conn: sqlite3.Connection,
    status: str,
) -> list[dict[str, Any]]:
    """Return all jobs with the given status, sorted by score descending."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC",
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_application_funnel(conn: sqlite3.Connection) -> dict[str, int]:
    """Return counts by status for the application funnel."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
    ).fetchall()
    return {row["status"]: row["cnt"] for row in rows}


# ---------------------------------------------------------------------------
# LLM Cache
# ---------------------------------------------------------------------------

def get_llm_cache(conn: sqlite3.Connection, jid: str) -> dict[str, Any] | None:
    """Return cached LLM response for a job, or None if not cached."""
    row = conn.execute(
        "SELECT response FROM llm_cache WHERE job_id = ?", (jid,)
    ).fetchone()
    if row:
        try:
            return json.loads(row["response"])
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def set_llm_cache(conn: sqlite3.Connection, jid: str, response: dict[str, Any]) -> None:
    """Cache an LLM response for a job."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO llm_cache (job_id, response, created) VALUES (?, ?, ?) "
        "ON CONFLICT(job_id) DO UPDATE SET response = ?, created = ?",
        (jid, json.dumps(response), now, json.dumps(response), now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Unscored backfill
# ---------------------------------------------------------------------------

def get_unscored_jobs(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Return jobs that have no score (NULL or 0), for backfill scoring."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE score IS NULL OR score = 0 "
        "ORDER BY first_seen DESC",
    ).fetchall()
    return [dict(row) for row in rows]


def update_score(
    conn: sqlite3.Connection,
    jid: str,
    score: int,
    matched_skills: list[str],
    flags: list[str],
) -> None:
    """Update score fields for a single job by ID."""
    conn.execute(
        "UPDATE jobs SET score = ?, matched_skills = ?, flags = ? WHERE id = ?",
        (score, json.dumps(matched_skills), json.dumps(flags), jid),
    )
    conn.commit()


def get_history(
    conn: sqlite3.Connection,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Return jobs first seen within the last *days* days, newest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE first_seen >= ? ORDER BY score DESC, first_seen DESC",
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Standalone self-test
# ---------------------------------------------------------------------------

def _demo() -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    print(f"Self-test using temp DB: {db_path}", file=sys.stderr)

    conn = init_db(db_path)

    sample = [
        {
            "title": "Director of Product",
            "company": "Acme Corp",
            "url": "https://example.com/1",
            "location": "Seattle, WA",
            "description": "Lead product strategy …",
            "source": "demo",
            "date_posted": "2026-04-01",
            "_score": {"score": 85, "matched_skills": ["roadmap"], "flags": []},
        },
        {
            "title": "VP of Product",
            "company": "BigCo",
            "url": "https://example.com/2",
            "location": "Remote",
            "description": "VP role …",
            "source": "demo",
            "date_posted": "2026-04-01",
            "_score": {"score": 72, "matched_skills": ["SaaS"], "flags": []},
        },
    ]

    jid1 = job_id("Director of Product", "Acme Corp", "https://example.com/1")
    assert not is_seen(conn, jid1), "should not be seen yet"

    n = save_jobs(conn, sample)
    assert n == 2, f"expected 2 inserts, got {n}"
    assert is_seen(conn, jid1), "should be seen now"

    # Re-save (upsert) — should insert 0
    n2 = save_jobs(conn, sample)
    assert n2 == 0, f"expected 0 new inserts on re-save, got {n2}"

    history = get_history(conn, days=1)
    assert len(history) == 2, f"expected 2 in history, got {len(history)}"

    conn.close()
    Path(db_path).unlink()
    print("All assertions passed.", file=sys.stderr)


if __name__ == "__main__":
    _demo()
