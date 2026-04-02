"""Generate a static HTML dashboard for GitHub Pages.

Reads the SQLite database and produces a self-contained HTML page with:
  - Pipeline stats (total jobs, sources, score distribution)
  - Top matches table with score bars and skill pills
  - Still-open jobs section
  - Recently closed jobs section
  - Source breakdown chart (CSS-only, no JS dependencies)
  - 7-day trend summary
  - Dark mode theme

Usage:
    python dashboard.py                 # writes docs/index.html
    python -m dashboard                 # same
"""

import json
import html as html_mod
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import db

DOCS_DIR = Path(__file__).resolve().parent / "docs"
OUTPUT_FILE = DOCS_DIR / "index.html"


def _esc(text: str) -> str:
    return html_mod.escape(str(text or ""), quote=True)


def _score_color(score: int) -> tuple[str, str]:
    if score >= 70:
        return "#22c55e", "Strong"
    if score >= 50:
        return "#eab308", "Moderate"
    return "#ef4444", "Weak"


def _score_bar_html(score: int, width: int = 160) -> str:
    bg, label = _score_color(score)
    fill = max(int(score / 100 * width), 6)
    return (
        f'<div style="background:#334155;border-radius:6px;height:18px;'
        f'width:{width}px;display:inline-block;vertical-align:middle;">'
        f'<div style="background:{bg};border-radius:6px;height:18px;'
        f'width:{fill}px;line-height:18px;color:#0f172a;font-size:11px;'
        f'font-weight:700;padding:0 6px;white-space:nowrap;">'
        f'{score}</div></div>'
    )


def _pill(text: str, bg: str = "#1e3a5f", fg: str = "#7dd3fc") -> str:
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;'
        f'margin:0 3px 3px 0;">{_esc(text)}</span>'
    )


def _stat_card(value: str, label: str, color: str = "#e2e8f0") -> str:
    return (
        f'<div style="background:rgba(255,255,255,0.08);border-radius:10px;'
        f'padding:14px 20px;text-align:center;min-width:90px;">'
        f'<div style="font-size:28px;font-weight:800;color:{color};">{value}</div>'
        f'<div style="font-size:11px;color:#94a3b8;margin-top:2px;">{label}</div>'
        f'</div>'
    )


def _bar_chart_row(label: str, count: int, max_count: int, color: str) -> str:
    pct = int(count / max(max_count, 1) * 100)
    return (
        f'<div style="display:flex;align-items:center;margin-bottom:6px;">'
        f'<div style="width:100px;font-size:12px;color:#94a3b8;">{_esc(label)}</div>'
        f'<div style="flex:1;background:#334155;border-radius:4px;height:20px;margin:0 8px;">'
        f'<div style="background:{color};border-radius:4px;height:20px;width:{pct}%;'
        f'min-width:2px;"></div></div>'
        f'<div style="font-size:12px;font-weight:600;color:#e2e8f0;width:30px;">{count}</div>'
        f'</div>'
    )


def _render_job_row(job: dict[str, Any]) -> str:
    title = _esc(job.get("title", ""))
    company = _esc(job.get("company", ""))
    location = _esc(job.get("location", "—"))
    url = _esc(job.get("url", "#"))
    score = job.get("score", 0) or 0
    source = _esc(job.get("source", ""))
    status = job.get("status", "open")

    skills_raw = job.get("matched_skills", "[]")
    try:
        skills = json.loads(skills_raw) if isinstance(skills_raw, str) else skills_raw
    except json.JSONDecodeError:
        skills = []
    skills_html = " ".join(_pill(s) for s in (skills or [])[:6])
    if len(skills or []) > 6:
        skills_html += f' <span style="color:#64748b;font-size:11px;">+{len(skills) - 6}</span>'

    flags_raw = job.get("flags", "[]")
    try:
        flags = json.loads(flags_raw) if isinstance(flags_raw, str) else flags_raw
    except json.JSONDecodeError:
        flags = []
    flags_html = " ".join(_pill(f, bg="#3b1818", fg="#fca5a5") for f in (flags or [])[:3])

    status_dot = "&#128994;" if status == "open" else "&#128308;"
    first_seen = job.get("first_seen", "")[:10]
    flags_div = f'<div style="margin-top:4px;">{flags_html}</div>' if flags_html else ""

    return (
        f'<tr style="border-bottom:1px solid #1e293b;">'
        f'<td style="padding:12px 16px;vertical-align:top;">'
        f'<div><a href="{url}" target="_blank" style="color:#60a5fa;font-size:14px;'
        f'font-weight:600;text-decoration:none;">{title}</a></div>'
        f'<div style="color:#94a3b8;font-size:13px;">{company}'
        f'<span style="color:#64748b;margin-left:8px;">{location}</span></div>'
        f'<div style="margin-top:4px;">{skills_html}</div>'
        f'{flags_div}'
        f'</td>'
        f'<td style="padding:12px 8px;vertical-align:top;text-align:center;">'
        f'{_score_bar_html(score)}</td>'
        f'<td style="padding:12px 8px;vertical-align:top;text-align:center;'
        f'font-size:12px;color:#94a3b8;">{source}</td>'
        f'<td style="padding:12px 8px;vertical-align:top;text-align:center;'
        f'font-size:12px;color:#94a3b8;">{status_dot} {first_seen}</td>'
        f'</tr>'
    )


def _render_tracking_section(funnel: dict[str, int]) -> str:
    statuses = [
        ("applied", "#3b82f6", "Applied"),
        ("interviewing", "#a78bfa", "Interviewing"),
        ("offer", "#22c55e", "Offer"),
        ("rejected", "#ef4444", "Rejected"),
        ("withdrawn", "#64748b", "Withdrawn"),
    ]
    rows = ""
    for key, color, label in statuses:
        count = funnel.get(key, 0)
        if count:
            rows += (
                f'<div style="display:flex;align-items:center;margin-bottom:8px;">'
                f'<div style="width:12px;height:12px;border-radius:50%;background:{color};'
                f'margin-right:10px;"></div>'
                f'<div style="color:#e2e8f0;font-size:14px;flex:1;">{label}</div>'
                f'<div style="color:{color};font-size:20px;font-weight:800;">{count}</div>'
                f'</div>'
            )
    if not rows:
        return ""
    return (
        f'<div class="card">'
        f'<h2>Application Tracking</h2>'
        f'{rows}'
        f'<div style="margin-top:10px;font-size:11px;color:#64748b;">'
        f'Use <code style="background:#334155;padding:2px 6px;border-radius:4px;">'
        f'python track.py set &lt;id&gt; applied</code> to track applications</div>'
        f'</div>'
    )


def generate_dashboard(conn: sqlite3.Connection, run_date: date | None = None) -> str:
    """Generate the full dashboard HTML from database state."""
    today = run_date or date.today()
    date_str = today.strftime("%B %d, %Y")

    # Load tracking overrides (separate DB, never overwritten by CI)
    try:
        import tracking
        track_conn = tracking.init_tracking()
        overrides = tracking.get_all_overrides(track_conn)
        track_funnel = tracking.get_funnel(track_conn)
        track_conn.close()
    except Exception:
        overrides = {}
        track_funnel = {}

    # Query data
    all_7d = db.get_history(conn, days=7)
    # Apply tracking overrides to status
    for j in all_7d:
        ov = overrides.get(j.get("id"))
        if ov:
            j["status"] = ov["status"]
    open_jobs = db.get_open_jobs(conn, days=7)
    closed_jobs = db.get_closed_jobs(conn, days=7)
    all_30d = db.get_history(conn, days=30)

    # Stats
    total_7d = len(all_7d)
    total_open = len(open_jobs)
    total_closed = len(closed_jobs)
    strong = sum(1 for j in all_7d if (j.get("score") or 0) >= 70)
    moderate = sum(1 for j in all_7d if 50 <= (j.get("score") or 0) < 70)
    tracked_count = sum(track_funnel.values())

    # Source breakdown
    sources: dict[str, int] = {}
    for j in all_7d:
        s = j.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    max_source = max(sources.values()) if sources else 1
    source_colors = {
        "career_pages": "#3b82f6",
        "adzuna": "#a78bfa",
        "remotive": "#34d399",
        "hn_hiring": "#fb923c",
    }

    # Score distribution
    score_buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "30-49": 0, "0-29": 0}
    for j in all_7d:
        s = j.get("score") or 0
        if s >= 90:
            score_buckets["90-100"] += 1
        elif s >= 70:
            score_buckets["70-89"] += 1
        elif s >= 50:
            score_buckets["50-69"] += 1
        elif s >= 30:
            score_buckets["30-49"] += 1
        else:
            score_buckets["0-29"] += 1
    max_bucket = max(score_buckets.values()) if any(score_buckets.values()) else 1
    bucket_colors = {
        "90-100": "#22c55e", "70-89": "#4ade80",
        "50-69": "#eab308", "30-49": "#fb923c", "0-29": "#ef4444",
    }

    # Daily trend (last 7 days)
    daily: dict[str, int] = {}
    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = 0
    for j in all_7d:
        fs = (j.get("first_seen") or "")[:10]
        if fs in daily:
            daily[fs] += 1

    # Top companies
    companies: dict[str, int] = {}
    for j in all_7d:
        c = j.get("company", "Unknown")
        companies[c] = companies.get(c, 0) + 1
    top_companies = sorted(companies.items(), key=lambda x: -x[1])[:10]
    max_company = top_companies[0][1] if top_companies else 1

    # Build sections — deduplicate by title+company for display
    seen_keys: set[str] = set()
    unique_jobs: list[dict[str, Any]] = []
    for j in sorted(all_7d, key=lambda j: -(j.get("score") or 0)):
        key = f"{(j.get('title') or '').lower()}|{(j.get('company') or '').lower()}"
        if key not in seen_keys:
            seen_keys.add(key)
            unique_jobs.append(j)
    top_jobs = unique_jobs[:20]
    top_rows = "\n".join(_render_job_row(j) for j in top_jobs)

    source_chart = "\n".join(
        _bar_chart_row(s, c, max_source, source_colors.get(s, "#64748b"))
        for s, c in sorted(sources.items(), key=lambda x: -x[1])
    )

    score_chart = "\n".join(
        _bar_chart_row(b, c, max_bucket, bucket_colors[b])
        for b, c in score_buckets.items()
    )

    company_chart = "\n".join(
        _bar_chart_row(c[:20], n, max_company, "#3b82f6")
        for c, n in top_companies
    )

    daily_chart = "\n".join(
        _bar_chart_row(d[5:], n, max(daily.values()) or 1, "#818cf8")
        for d, n in sorted(daily.items())
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Winters Job Finder — Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0f172a; font-family: -apple-system, BlinkMacSystemFont,
      'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #e2e8f0; }}
    .wrap {{ max-width: 960px; margin: 0 auto; padding: 24px 16px; }}
    .header {{ background: linear-gradient(135deg, #0f2744, #1d4ed8);
      border-radius: 14px; padding: 28px 32px; color: #fff; margin-bottom: 24px;
      border: 1px solid #1e3a5f; }}
    .header h1 {{ font-size: 24px; font-weight: 800; }}
    .header p {{ font-size: 14px; opacity: 0.85; margin-top: 4px; }}
    .stats {{ display: flex; gap: 12px; margin-top: 18px; flex-wrap: wrap; }}
    .card {{ background: #1e293b; border-radius: 12px; padding: 20px 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.3); margin-bottom: 16px;
      border: 1px solid #334155; }}
    .card h2 {{ font-size: 16px; color: #e2e8f0; margin-bottom: 14px;
      padding-bottom: 10px; border-bottom: 2px solid #334155; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    @media (max-width: 640px) {{ .grid {{ grid-template-columns: 1fr; }} }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; padding: 10px 16px; font-size: 12px;
      color: #94a3b8; font-weight: 600; border-bottom: 2px solid #334155; }}
    .footer {{ text-align: center; padding: 24px 0; color: #64748b; font-size: 12px; }}
    a {{ color: #60a5fa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; color: #93c5fd; }}
    .legend {{ display: flex; gap: 20px; align-items: center; padding: 12px 24px;
      border-top: 1px solid #334155; font-size: 12px; color: #94a3b8; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  </style>
</head>
<body>
  <div class="wrap">

    <!-- Header -->
    <div class="header">
      <h1>Winters Job Finder</h1>
      <p>Dashboard &mdash; {date_str}</p>
      <div class="stats">
        {_stat_card(str(total_7d), "Jobs (7d)", "#e2e8f0")}
        {_stat_card(str(total_open), "Open", "#4ade80")}
        {_stat_card(str(strong), "Strong 70+", "#4ade80")}
        {_stat_card(str(moderate), "Moderate 50+", "#facc15")}
        {_stat_card(str(total_closed), "Closed", "#f87171")}
        {_stat_card(str(len(all_30d)), "Total (30d)", "#93c5fd")}
        {_stat_card(str(tracked_count), "Tracked", "#c084fc") if tracked_count else ""}
      </div>
    </div>

    <!-- Charts Grid -->
    <div class="grid">
      <div class="card">
        <h2>Score Distribution (7d)</h2>
        {score_chart}
      </div>
      <div class="card">
        <h2>By Source (7d)</h2>
        {source_chart}
      </div>
      <div class="card">
        <h2>Daily New Jobs</h2>
        {daily_chart}
      </div>
      <div class="card">
        <h2>Top Companies (7d)</h2>
        {company_chart}
      </div>
    </div>

    <!-- Application Tracking -->
    {_render_tracking_section(track_funnel) if tracked_count else ""}

    <!-- Top Matches -->
    <div class="card">
      <h2>Top 20 Matches (7d)</h2>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th style="text-align:center;">Score</th>
              <th style="text-align:center;">Source</th>
              <th style="text-align:center;">Status</th>
            </tr>
          </thead>
          <tbody>
            {top_rows if top_rows else '<tr><td colspan="4" style="padding:24px;text-align:center;color:#64748b;">No jobs in the last 7 days. Run the pipeline first.</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="legend">
        <span style="color:#64748b;font-weight:600;">Legend:</span>
        <div class="legend-item">&#128994;<span>Open &mdash; still listed on career page</span></div>
        <div class="legend-item">&#128308;<span>Closed &mdash; no longer found on career page (likely filled or removed)</span></div>
        <div class="legend-item">
          <span style="background:#22c55e;color:#0f172a;font-size:10px;font-weight:700;
            padding:1px 6px;border-radius:4px;">70+</span>
          <span>Strong match</span>
        </div>
        <div class="legend-item">
          <span style="background:#eab308;color:#0f172a;font-size:10px;font-weight:700;
            padding:1px 6px;border-radius:4px;">50-69</span>
          <span>Moderate</span>
        </div>
        <div class="legend-item">
          <span style="background:#ef4444;color:#fff;font-size:10px;font-weight:700;
            padding:1px 6px;border-radius:4px;">&lt;50</span>
          <span>Weak</span>
        </div>
      </div>
    </div>

    <div class="footer">
      Winters Job Finder &middot; Generated {generated_at} &middot;
      <a href="https://github.com/greenleadr/winters-job-finder">GitHub</a>
    </div>

  </div>
</body>
</html>"""


def build(db_path: str | Path | None = None) -> Path:
    """Generate the dashboard and write to docs/index.html."""
    DOCS_DIR.mkdir(exist_ok=True)
    conn = db.init_db(db_path)
    html = generate_dashboard(conn)
    conn.close()
    OUTPUT_FILE.write_text(html)
    print(f"Dashboard written to {OUTPUT_FILE}", file=sys.stderr)
    return OUTPUT_FILE


if __name__ == "__main__":
    build()
