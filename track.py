"""Manually update job application status.

Usage:
    python track.py list                         # show application funnel
    python track.py search "brex"                # find jobs by keyword
    python track.py set <job_id> applied         # mark as applied
    python track.py set <job_id> interviewing    # mark as interviewing
    python track.py set <job_id> offer           # mark as offer
    python track.py set <job_id> rejected        # mark as rejected
"""

import sys
import db


def _print_job(j: dict) -> None:
    score = j.get("score") or 0
    status = j.get("status", "?")
    jid = j.get("id", "?")
    print(f"  [{jid}]  {score:>3}  {status:12}  {j.get('title', '')}  @  {j.get('company', '')}")


def cmd_list(conn):
    funnel = db.get_application_funnel(conn)
    print("Application Funnel:")
    for status in ["open", "applied", "interviewing", "offer", "rejected", "closed"]:
        count = funnel.get(status, 0)
        bar = "#" * min(count, 50)
        print(f"  {status:14} {count:>5}  {bar}")


def cmd_search(conn, keyword: str):
    keyword = keyword.lower()
    rows = conn.execute(
        "SELECT * FROM jobs WHERE LOWER(title) LIKE ? OR LOWER(company) LIKE ? "
        "ORDER BY score DESC LIMIT 20",
        (f"%{keyword}%", f"%{keyword}%"),
    ).fetchall()
    print(f"Found {len(rows)} matches for '{keyword}':")
    for r in rows:
        _print_job(dict(r))


def cmd_set(conn, jid: str, status: str):
    try:
        ok = db.set_status(conn, jid, status)
        if ok:
            print(f"Updated {jid} → {status}")
        else:
            print(f"Job ID '{jid}' not found")
    except ValueError as e:
        print(e)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    conn = db.init_db()
    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list(conn)
    elif cmd == "search" and len(sys.argv) >= 3:
        cmd_search(conn, sys.argv[2])
    elif cmd == "set" and len(sys.argv) >= 4:
        cmd_set(conn, sys.argv[2], sys.argv[3])
    else:
        print(__doc__)

    conn.close()


if __name__ == "__main__":
    main()
