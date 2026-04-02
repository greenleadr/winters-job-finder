"""Manually update job application status.

Statuses are stored in a separate tracking.db that CI never overwrites.
Job details are read from the main jobs.db for display.

Usage:
    python track.py list                              # show application funnel
    python track.py search "brex"                     # find jobs by keyword
    python track.py set <job_id> applied              # mark as applied
    python track.py set <job_id> applied "sent resume via email"  # with notes
    python track.py set <job_id> interviewing         # update status
    python track.py set <job_id> offer                # mark as offer
    python track.py set <job_id> rejected             # mark as rejected
    python track.py set <job_id> withdrawn            # mark as withdrawn
    python track.py reset <job_id>                    # remove tracking override
    python track.py show applied                      # list all applied jobs
    python track.py show interviewing                 # list all interviewing jobs
"""

import sys
import db
import tracking


def _get_job_detail(jobs_conn, job_id: str) -> dict | None:
    row = jobs_conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _print_job(j: dict, override: dict | None = None) -> None:
    score = j.get("score") or 0
    status = override["status"] if override else j.get("status", "open")
    jid = j.get("id", "?")
    notes = f'  "{override["notes"]}"' if override and override.get("notes") else ""
    print(f"  [{jid}]  {score:>3}  {status:14}  {j.get('title', '')}  @  {j.get('company', '')}{notes}")


def cmd_list(track_conn):
    funnel = tracking.get_funnel(track_conn)
    total = sum(funnel.values())
    print(f"Application Tracking ({total} jobs):")
    for status in ["applied", "interviewing", "offer", "rejected", "withdrawn"]:
        count = funnel.get(status, 0)
        bar = "#" * min(count, 40)
        print(f"  {status:14} {count:>4}  {bar}")
    if not total:
        print("  (none yet — use 'track.py set <job_id> applied' to start tracking)")


def cmd_search(jobs_conn, track_conn, keyword: str):
    keyword = keyword.lower()
    rows = jobs_conn.execute(
        "SELECT * FROM jobs WHERE LOWER(title) LIKE ? OR LOWER(company) LIKE ? "
        "ORDER BY score DESC LIMIT 20",
        (f"%{keyword}%", f"%{keyword}%"),
    ).fetchall()
    overrides = tracking.get_all_overrides(track_conn)
    print(f"Found {len(rows)} matches for '{keyword}':")
    for r in rows:
        j = dict(r)
        override = overrides.get(j["id"])
        _print_job(j, override)


def cmd_set(jobs_conn, track_conn, job_id: str, status: str, notes: str = ""):
    # Verify the job exists
    job = _get_job_detail(jobs_conn, job_id)
    if not job:
        print(f"Job ID '{job_id}' not found in jobs.db")
        return

    try:
        tracking.set_status(track_conn, job_id, status, notes)
        print(f"Tracked: {job['title']} @ {job['company']} → {status}")
        if notes:
            print(f"  Notes: {notes}")
    except ValueError as e:
        print(e)


def cmd_reset(track_conn, job_id: str):
    if tracking.remove_status(track_conn, job_id):
        print(f"Removed tracking for {job_id}")
    else:
        print(f"Job ID '{job_id}' was not being tracked")


def cmd_show(jobs_conn, track_conn, status: str):
    tracked = tracking.get_by_status(track_conn, status)
    if not tracked:
        print(f"No jobs with status '{status}'")
        return
    print(f"Jobs with status '{status}' ({len(tracked)}):")
    for t in tracked:
        job = _get_job_detail(jobs_conn, t["job_id"])
        if job:
            _print_job(job, t)
        else:
            print(f"  [{t['job_id']}]  (job no longer in DB)  status={t['status']}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    jobs_conn = db.init_db()
    track_conn = tracking.init_tracking()
    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list(track_conn)
    elif cmd == "search" and len(sys.argv) >= 3:
        cmd_search(jobs_conn, track_conn, sys.argv[2])
    elif cmd == "set" and len(sys.argv) >= 4:
        notes = sys.argv[4] if len(sys.argv) >= 5 else ""
        cmd_set(jobs_conn, track_conn, sys.argv[2], sys.argv[3], notes)
    elif cmd == "reset" and len(sys.argv) >= 3:
        cmd_reset(track_conn, sys.argv[2])
    elif cmd == "show" and len(sys.argv) >= 3:
        cmd_show(jobs_conn, track_conn, sys.argv[2])
    else:
        print(__doc__)

    jobs_conn.close()
    track_conn.close()


if __name__ == "__main__":
    main()
