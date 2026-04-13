"""Generate and send a weekly summary digest.

Queries the SQLite database for the week's trends and sends a summary
email with: total new jobs, top companies hiring, score distribution,
application funnel, jobs that disappeared, and top matches.

Usage:
    python weekly_summary.py              # generate and send
    SKIP_EMAIL=true python weekly_summary.py  # generate only (print HTML)
"""

import json
import html as html_mod
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import db


def _esc(text: str) -> str:
    return html_mod.escape(str(text or ""), quote=True)


def _stat_row(label: str, value: str, color: str = "#1e293b") -> str:
    return (
        f'<tr><td style="padding:8px 16px;color:#64748b;font-size:14px;">{_esc(label)}</td>'
        f'<td style="padding:8px 16px;font-size:14px;font-weight:700;color:{color};">'
        f'{_esc(value)}</td></tr>'
    )


def _bar(label: str, count: int, max_c: int, color: str) -> str:
    pct = int(count / max(max_c, 1) * 100)
    return (
        f'<div style="display:flex;align-items:center;margin-bottom:5px;">'
        f'<div style="width:120px;font-size:13px;color:#475569;">{_esc(label)}</div>'
        f'<div style="flex:1;background:#e5e7eb;border-radius:4px;height:18px;margin:0 8px;">'
        f'<div style="background:{color};border-radius:4px;height:18px;width:{pct}%;'
        f'min-width:2px;"></div></div>'
        f'<div style="font-size:13px;font-weight:600;width:30px;">{count}</div></div>'
    )


def _job_row(j: dict[str, Any]) -> str:
    title = _esc(j.get("title", ""))
    company = _esc(j.get("company", ""))
    url = _esc(j.get("url", "#"))
    score = j.get("score") or 0
    bg = "#16a34a" if score >= 70 else "#ca8a04" if score >= 50 else "#dc2626"
    return (
        f'<tr style="border-bottom:1px solid #f1f5f9;">'
        f'<td style="padding:8px 16px;"><a href="{url}" style="color:#1d4ed8;'
        f'font-weight:600;text-decoration:none;">{title}</a>'
        f'<span style="color:#64748b;margin-left:8px;">{company}</span></td>'
        f'<td style="padding:8px;text-align:center;">'
        f'<span style="background:{bg};color:#fff;font-size:11px;font-weight:700;'
        f'padding:2px 8px;border-radius:8px;">{score}</span></td></tr>'
    )


def generate_weekly_summary(conn, run_date: date | None = None) -> str:
    today = run_date or date.today()
    week_start = today - timedelta(days=7)
    date_str = today.strftime("%B %d, %Y")
    week_range = f"{week_start.strftime('%b %d')} – {today.strftime('%b %d, %Y')}"

    # Data queries
    all_week = db.get_history(conn, days=7)
    open_jobs = db.get_open_jobs(conn, days=7)
    closed_jobs = db.get_closed_jobs(conn, days=7)
    long_open = db.get_long_open_jobs(conn, min_days=7, max_days=30)
    funnel = db.get_application_funnel(conn)

    total = len(all_week)
    strong = sum(1 for j in all_week if (j.get("score") or 0) >= 70)
    moderate = sum(1 for j in all_week if 50 <= (j.get("score") or 0) < 70)

    # Top companies
    companies: dict[str, int] = {}
    for j in all_week:
        c = j.get("company", "Unknown")
        companies[c] = companies.get(c, 0) + 1
    top_companies = sorted(companies.items(), key=lambda x: -x[1])[:10]
    max_co = top_companies[0][1] if top_companies else 1
    co_chart = "\n".join(_bar(c[:25], n, max_co, "#3b82f6") for c, n in top_companies)

    # Source breakdown
    sources: dict[str, int] = {}
    for j in all_week:
        s = j.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    src_colors = {"career_pages": "#2563eb", "adzuna": "#7c3aed", "remotive": "#059669", "hn_hiring": "#ea580c"}
    max_src = max(sources.values()) if sources else 1
    src_chart = "\n".join(
        _bar(s, c, max_src, src_colors.get(s, "#64748b"))
        for s, c in sorted(sources.items(), key=lambda x: -x[1])
    )

    # Top 10 matches
    top_10 = sorted(all_week, key=lambda j: -(j.get("score") or 0))[:10]
    # Dedupe by title+company
    seen: set[str] = set()
    unique_top: list[dict[str, Any]] = []
    for j in top_10:
        key = f"{(j.get('title') or '').lower()}|{(j.get('company') or '').lower()}"
        if key not in seen:
            seen.add(key)
            unique_top.append(j)
    top_rows = "\n".join(_job_row(j) for j in unique_top)

    # Funnel
    funnel_html = ""
    for status, label, color in [
        ("open", "Open", "#16a34a"), ("applied", "Applied", "#2563eb"),
        ("interviewing", "Interviewing", "#7c3aed"), ("offer", "Offer", "#059669"),
        ("rejected", "Rejected", "#dc2626"), ("closed", "Closed", "#64748b"),
    ]:
        count = funnel.get(status, 0)
        funnel_html += _stat_row(label, str(count), color)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:
  -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:680px;margin:0 auto;padding:24px 16px;">

    <div style="background:linear-gradient(135deg,#1e3a5f,#7c3aed);border-radius:12px;
                padding:28px 32px;color:#fff;margin-bottom:24px;">
      <h1 style="margin:0 0 4px;font-size:22px;font-weight:800;">Weekly Summary</h1>
      <p style="margin:0;font-size:14px;opacity:0.85;">{week_range}</p>
      <div style="margin-top:16px;display:flex;gap:16px;flex-wrap:wrap;">
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;">{total}</div>
          <div style="font-size:11px;opacity:0.8;">New Jobs</div></div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#4ade80;">{strong}</div>
          <div style="font-size:11px;opacity:0.8;">Strong (70+)</div></div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#facc15;">{moderate}</div>
          <div style="font-size:11px;opacity:0.8;">Moderate</div></div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#f87171;">{len(closed_jobs)}</div>
          <div style="font-size:11px;opacity:0.8;">Closed</div></div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#93c5fd;">{len(long_open)}</div>
          <div style="font-size:11px;opacity:0.8;">Open 7+ days</div></div>
      </div>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <div style="background:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
        <h2 style="font-size:16px;margin:0 0 12px;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">Top Companies</h2>
        {co_chart}
      </div>
      <div style="background:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
        <h2 style="font-size:16px;margin:0 0 12px;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">By Source</h2>
        {src_chart}
      </div>
    </div>

    <div style="background:#fff;border-radius:12px;padding:20px 24px;margin-top:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
      <h2 style="font-size:16px;margin:0 0 12px;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">Application Funnel</h2>
      <table style="width:100%;border-collapse:collapse;">{funnel_html}</table>
    </div>

    <div style="background:#fff;border-radius:12px;padding:20px 24px;margin-top:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
      <h2 style="font-size:16px;margin:0 0 12px;border-bottom:2px solid #e5e7eb;padding-bottom:8px;">Top Matches This Week</h2>
      <table style="width:100%;border-collapse:collapse;">{top_rows}</table>
    </div>

    <div style="text-align:center;padding:24px 0 8px;color:#94a3b8;font-size:12px;">
      Winters Job Finder &middot; Weekly Summary &middot; {date_str}
    </div>

  </div>
</body>
</html>"""


def run() -> None:
    conn = db.init_db()
    today = date.today()
    html_body = generate_weekly_summary(conn, run_date=today)

    skip_email = os.environ.get("SKIP_EMAIL", "").lower() == "true"
    if skip_email:
        print(html_body)
        print("SKIP_EMAIL=true — printed HTML", file=sys.stderr)
    else:
        from emailer import send_digest
        subject_date = today.strftime("%Y-%m-%d")
        send_digest(
            html_body,
            job_count=len(db.get_history(conn, days=7)),
            run_date=today,
        )

    conn.close()


if __name__ == "__main__":
    run()
