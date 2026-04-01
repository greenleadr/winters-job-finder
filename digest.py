"""Generate an HTML email digest from scored job postings.

Expects jobs in the format returned by ``scorer.score_jobs`` — each dict has
the original job fields plus a ``_score`` key with score, matched_skills,
gaps, and flags.

Usage:
    python digest.py          # renders a demo digest and writes digest_preview.html
    python -m digest          # same
"""

import html
import json
from datetime import date
from typing import Any

TOP_N = 10


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _score_color(score: int) -> tuple[str, str]:
    """Return (background, label) for a score badge."""
    if score >= 70:
        return "#16a34a", "Strong"
    if score >= 50:
        return "#ca8a04", "Moderate"
    return "#dc2626", "Weak"


def _score_bar(score: int) -> str:
    bg, label = _score_color(score)
    width = max(score, 8)  # minimum visible width
    return (
        f'<div style="background:#e5e7eb;border-radius:6px;height:22px;'
        f'width:200px;display:inline-block;vertical-align:middle;">'
        f'<div style="background:{bg};border-radius:6px;height:22px;'
        f'width:{width * 2}px;max-width:200px;line-height:22px;'
        f'color:#fff;font-size:12px;font-weight:700;padding:0 8px;'
        f'white-space:nowrap;">{score} — {label}</div></div>'
    )


def _badge(text: str, bg: str = "#ef4444", fg: str = "#fff") -> str:
    return (
        f'<span style="display:inline-block;background:{bg};color:{fg};'
        f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;'
        f'margin:0 4px 4px 0;">{_esc(text)}</span>'
    )


def _skill_pill(text: str) -> str:
    return _badge(text, bg="#e0f2fe", fg="#0369a1")


def _flag_pill(text: str) -> str:
    return _badge(text, bg="#fef2f2", fg="#b91c1c")


def _render_job_row(job: dict[str, Any], rank: int) -> str:
    s = job.get("_score", {})
    score: int = s.get("score", 0)
    matched: list[str] = s.get("matched_skills", [])
    flags: list[str] = s.get("flags", [])

    title = _esc(job.get("title", "Unknown"))
    company = _esc(job.get("company", "Unknown"))
    location = _esc(job.get("location", "—"))
    url = _esc(job.get("url", "#"))
    source = _esc(job.get("source", ""))
    date_posted = _esc(job.get("date_posted", ""))

    skills_html = " ".join(_skill_pill(sk) for sk in matched[:8])
    if len(matched) > 8:
        skills_html += f' <span style="color:#64748b;font-size:12px;">+{len(matched) - 8} more</span>'

    flags_html = " ".join(_flag_pill(f) for f in flags) if flags else ""

    meta_parts = [location]
    if source:
        meta_parts.append(source)
    if date_posted:
        meta_parts.append(date_posted)
    meta = " &middot; ".join(meta_parts)

    return f"""
    <tr style="border-bottom:1px solid #e5e7eb;">
      <td style="padding:16px;vertical-align:top;width:36px;color:#94a3b8;
                 font-size:18px;font-weight:700;text-align:center;">
        {rank}
      </td>
      <td style="padding:16px;">
        <div style="margin-bottom:4px;">
          <a href="{url}" style="color:#1d4ed8;font-size:16px;font-weight:700;
                                  text-decoration:none;">{title}</a>
          <span style="color:#475569;font-size:14px;margin-left:8px;">{company}</span>
        </div>
        <div style="margin-bottom:6px;">{_score_bar(score)}</div>
        <div style="color:#64748b;font-size:13px;margin-bottom:6px;">{meta}</div>
        <div style="margin-bottom:4px;">{skills_html}</div>
        {f'<div style="margin-top:6px;">{flags_html}</div>' if flags_html else ''}
      </td>
    </tr>"""


def generate_digest(
    scored_jobs: list[dict[str, Any]],
    run_date: date | None = None,
) -> str:
    """Return an HTML email body for the given scored job list.

    *scored_jobs* should already contain ``_score`` dicts (as produced by
    ``scorer.score_jobs``).  They are re-sorted by score descending here for
    safety.
    """
    today = run_date or date.today()
    date_str = today.strftime("%B %d, %Y")
    total = len(scored_jobs)

    # Sort descending by score
    jobs = sorted(
        scored_jobs,
        key=lambda j: j.get("_score", {}).get("score", 0),
        reverse=True,
    )
    top = jobs[:TOP_N]

    strong = sum(1 for j in jobs if j.get("_score", {}).get("score", 0) >= 70)
    moderate = sum(1 for j in jobs if 50 <= j.get("_score", {}).get("score", 0) < 70)

    # Build job rows
    rows_html = "\n".join(_render_job_row(j, i + 1) for i, j in enumerate(top))

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:
  -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

  <!-- Wrapper -->
  <div style="max-width:680px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1e3a5f,#2563eb);border-radius:12px;
                padding:28px 32px;color:#fff;margin-bottom:24px;">
      <h1 style="margin:0 0 4px;font-size:22px;font-weight:800;">
        Winters Job Finder
      </h1>
      <p style="margin:0;font-size:14px;opacity:0.85;">{date_str}</p>
      <div style="margin-top:16px;display:flex;gap:16px;">
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;
                    padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;">{total}</div>
          <div style="font-size:11px;opacity:0.8;">Jobs Found</div>
        </div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;
                    padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#4ade80;">{strong}</div>
          <div style="font-size:11px;opacity:0.8;">Strong (70+)</div>
        </div>
        <div style="background:rgba(255,255,255,0.15);border-radius:8px;
                    padding:10px 18px;text-align:center;">
          <div style="font-size:26px;font-weight:800;color:#facc15;">{moderate}</div>
          <div style="font-size:11px;opacity:0.8;">Moderate (50-69)</div>
        </div>
      </div>
    </div>

    <!-- Top Matches -->
    <div style="background:#fff;border-radius:12px;overflow:hidden;
                box-shadow:0 1px 3px rgba(0,0,0,0.08);">
      <div style="padding:18px 24px;border-bottom:2px solid #e5e7eb;">
        <h2 style="margin:0;font-size:17px;color:#1e293b;">
          Top {min(TOP_N, total)} Matches
        </h2>
      </div>
      <table style="width:100%;border-collapse:collapse;">
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="text-align:center;padding:24px 0 8px;color:#94a3b8;font-size:12px;">
      Winters Job Finder &middot; automated digest &middot; {date_str}
    </div>

  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    from scorer import _DEMO_JOBS, load_profile, score_jobs

    profile = load_profile()
    scored = score_jobs(_DEMO_JOBS, profile)

    html_body = generate_digest(scored)
    out = "digest_preview.html"
    with open(out, "w") as f:
        f.write(html_body)

    print(f"Digest preview written to {out}")
    print(f"Jobs: {len(scored)}, top score: {scored[0]['_score']['score']}")


if __name__ == "__main__":
    _demo()
