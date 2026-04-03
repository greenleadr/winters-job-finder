"""Process GitHub Issues for tracking and feedback.

Called by the track-issue.yml workflow when an issue is opened with
a [TRACK] or [FEEDBACK] prefix in the title.

Environment variables:
    ISSUE_TITLE   — GitHub Issue title (e.g., "[TRACK] abc123 applied")
    ISSUE_BODY    — GitHub Issue body text
    ISSUE_NUMBER  — GitHub Issue number

Usage:
    ISSUE_TITLE="[TRACK] abc123 applied" python process_issue.py
"""

import os
import re
import sys

import db
import tracking


def process_track(title: str, body: str, issue_num: str) -> bool:
    """Parse and process a [TRACK] issue."""
    m = re.match(r"\[TRACK\]\s+(\w+)\s+(\w+)", title)
    if not m:
        print(f"Could not parse TRACK title: {title}", file=sys.stderr)
        return False

    job_id = m.group(1)
    status = m.group(2)

    # Validate job exists
    conn = db.init_db()
    row = conn.execute("SELECT title, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    if not row:
        print(f"Job ID '{job_id}' not found in jobs.db", file=sys.stderr)
        return False

    # Validate status
    if status not in tracking._VALID_STATUSES:
        print(
            f"Invalid status '{status}'. Valid: {sorted(tracking._VALID_STATUSES)}",
            file=sys.stderr,
        )
        return False

    # Update tracking
    track_conn = tracking.init_tracking()
    notes = f"via issue #{issue_num}"
    tracking.set_status(track_conn, job_id, status, notes=notes)
    track_conn.close()

    print(f"TRACK: {row['title']} @ {row['company']} → {status}")
    return True


def process_feedback(title: str, body: str, issue_num: str) -> bool:
    """Parse and process a [FEEDBACK] issue."""
    m = re.match(r"\[FEEDBACK\]\s+(\w+)\s+(\w+)\s+(\w+)", title)
    if not m:
        print(f"Could not parse FEEDBACK title: {title}", file=sys.stderr)
        return False

    job_id = m.group(1)
    field = m.group(2)
    rating = m.group(3)

    # Validate job exists
    conn = db.init_db()
    row = conn.execute("SELECT title, company FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    if not row:
        print(f"Job ID '{job_id}' not found in jobs.db", file=sys.stderr)
        return False

    # Store feedback
    track_conn = tracking.init_tracking()
    try:
        tracking.add_feedback(track_conn, job_id, field, rating)
    except ValueError as e:
        print(f"Feedback error: {e}", file=sys.stderr)
        track_conn.close()
        return False
    track_conn.close()

    print(f"FEEDBACK: {row['title']} @ {row['company']} — {field} → {rating}")
    return True


def main():
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    issue_num = os.environ.get("ISSUE_NUMBER", "?")

    if not title:
        print("No ISSUE_TITLE set", file=sys.stderr)
        sys.exit(1)

    if title.startswith("[TRACK]"):
        ok = process_track(title, body, issue_num)
    elif title.startswith("[FEEDBACK]"):
        ok = process_feedback(title, body, issue_num)
    else:
        print(f"Unknown issue type: {title}", file=sys.stderr)
        ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
