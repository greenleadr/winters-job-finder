"""Generate an interactive static HTML dashboard for GitHub Pages.

Embeds all job data as JSON and uses vanilla JavaScript for:
  - Filtering by score, status, source, keyword
  - Sorting by score, date, company
  - Expandable job detail panels with LLM insights
  - Application tracking via GitHub Issue creation
  - Model feedback via GitHub Issue creation

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
REPO = "greenleadr/winters-job-finder"
MIN_SCORE_FOR_JSON = 0  # include all scored jobs in JSON blob


def _esc(text: str) -> str:
    return html_mod.escape(str(text or ""), quote=True)


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


def _build_jobs_json(
    all_jobs: list[dict[str, Any]],
    overrides: dict[str, dict[str, str]],
    llm_cache: dict[str, dict[str, Any]],
    feedback_by_job: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Build the JSON data blob for client-side rendering."""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []

    for j in sorted(all_jobs, key=lambda x: -(x.get("score") or 0)):
        key = f"{(j.get('title') or '').lower()}|{(j.get('company') or '').lower()}"
        if key in seen:
            continue
        seen.add(key)

        jid = j.get("id", "")
        score = j.get("score") or 0
        if score < MIN_SCORE_FOR_JSON:
            continue

        # Parse JSON fields
        skills_raw = j.get("matched_skills", "[]")
        try:
            skills = json.loads(skills_raw) if isinstance(skills_raw, str) else (skills_raw or [])
        except json.JSONDecodeError:
            skills = []

        flags_raw = j.get("flags", "[]")
        try:
            flags = json.loads(flags_raw) if isinstance(flags_raw, str) else (flags_raw or [])
        except json.JSONDecodeError:
            flags = []

        # Get tracking override
        ov = overrides.get(jid, {})
        status = ov.get("status") if ov else j.get("status", "open")

        # Get LLM data
        llm = llm_cache.get(jid, {})

        # Get feedback
        fb = feedback_by_job.get(jid, [])

        desc = j.get("description", "") or ""

        result.append({
            "id": jid,
            "title": j.get("title", ""),
            "company": j.get("company", ""),
            "location": j.get("location", ""),
            "url": j.get("url", ""),
            "score": score,
            "source": j.get("source", ""),
            "status": status,
            "first_seen": (j.get("first_seen") or "")[:10],
            "matched_skills": skills,
            "flags": flags,
            "salary_min": j.get("salary_min"),
            "salary_max": j.get("salary_max"),
            "desc_preview": desc[:400],
            "llm": llm if llm else None,
            "tracking": {"status": ov.get("status"), "notes": ov.get("notes", ""), "updated": ov.get("updated", "")} if ov else None,
            "feedback": [{"field": f.get("field"), "rating": f.get("rating")} for f in fb] if fb else [],
        })

    return result


def generate_dashboard(conn: sqlite3.Connection, run_date: date | None = None) -> str:
    today = run_date or date.today()
    date_str = today.strftime("%B %d, %Y")

    # Load tracking + feedback
    try:
        import tracking
        track_conn = tracking.init_tracking()
        overrides = tracking.get_all_overrides(track_conn)
        track_funnel = tracking.get_funnel(track_conn)
        feedback_stats = tracking.get_feedback_stats(track_conn)
        # Build per-job feedback lookup
        fb_rows = track_conn.execute("SELECT * FROM feedback").fetchall()
        feedback_by_job: dict[str, list[dict[str, Any]]] = {}
        for row in fb_rows:
            r = dict(row)
            feedback_by_job.setdefault(r["job_id"], []).append(r)
        track_conn.close()
    except Exception:
        overrides = {}
        track_funnel = {}
        feedback_stats = {}
        feedback_by_job = {}

    # Load LLM cache
    llm_cache = db.get_all_llm_cache(conn)

    # Query data
    all_7d = db.get_history(conn, days=7)
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

    # Charts (server-rendered)
    sources: dict[str, int] = {}
    for j in all_7d:
        s = j.get("source", "unknown")
        sources[s] = sources.get(s, 0) + 1
    max_source = max(sources.values()) if sources else 1
    source_colors = {"career_pages": "#3b82f6", "adzuna": "#a78bfa", "remotive": "#34d399", "hn_hiring": "#fb923c"}

    score_buckets = {"90-100": 0, "70-89": 0, "50-69": 0, "30-49": 0, "0-29": 0}
    for j in all_7d:
        s = j.get("score") or 0
        if s >= 90: score_buckets["90-100"] += 1
        elif s >= 70: score_buckets["70-89"] += 1
        elif s >= 50: score_buckets["50-69"] += 1
        elif s >= 30: score_buckets["30-49"] += 1
        else: score_buckets["0-29"] += 1
    max_bucket = max(score_buckets.values()) if any(score_buckets.values()) else 1
    bucket_colors = {"90-100": "#22c55e", "70-89": "#4ade80", "50-69": "#eab308", "30-49": "#fb923c", "0-29": "#ef4444"}

    daily: dict[str, int] = {}
    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        daily[d] = 0
    for j in all_7d:
        fs = (j.get("first_seen") or "")[:10]
        if fs in daily:
            daily[fs] += 1

    companies: dict[str, int] = {}
    for j in all_7d:
        c = j.get("company", "Unknown")
        companies[c] = companies.get(c, 0) + 1
    top_companies = sorted(companies.items(), key=lambda x: -x[1])[:10]
    max_company = top_companies[0][1] if top_companies else 1

    source_chart = "\n".join(_bar_chart_row(s, c, max_source, source_colors.get(s, "#64748b")) for s, c in sorted(sources.items(), key=lambda x: -x[1]))
    score_chart = "\n".join(_bar_chart_row(b, c, max_bucket, bucket_colors[b]) for b, c in score_buckets.items())
    company_chart = "\n".join(_bar_chart_row(c[:20], n, max_company, "#3b82f6") for c, n in top_companies)
    daily_chart = "\n".join(_bar_chart_row(d[5:], n, max(daily.values()) or 1, "#818cf8") for d, n in sorted(daily.items()))

    # Feedback accuracy card
    fb_card = ""
    if feedback_stats:
        for field, counts in feedback_stats.items():
            total = counts.get("accurate", 0) + counts.get("inaccurate", 0)
            acc = counts.get("accurate", 0)
            pct = int(acc / total * 100) if total else 0
            fb_card += _stat_card(f"{pct}%", f"{field.title()} Accuracy", "#c084fc" if pct >= 70 else "#f87171")

    # Build JSON blob
    jobs_json = _build_jobs_json(all_7d, overrides, llm_cache, feedback_by_job)
    jobs_json_str = json.dumps(jobs_json, separators=(",", ":"))

    # Build profile scoring data for client-side evaluation
    try:
        from scorer import load_profile, _load_target_companies
        _profile = load_profile()
        profile_scoring = {
            "target_titles": _profile.get("target_titles", []),
            "skills": _profile.get("skills", {}),
            "skill_variants": _profile.get("skill_variants", {}),
            "target_companies": list(_load_target_companies()),
            "total_years": _profile.get("experience", {}).get("total_years", 0),
            "industries": _profile.get("experience", {}).get("industries", []),
            "company_sizes": _profile.get("preferences", {}).get("company_size", []),
        }
    except Exception:
        profile_scoring = {"target_titles": [], "skills": {}, "skill_variants": {}, "target_companies": [], "total_years": 15, "industries": [], "company_sizes": []}
    profile_json_str = json.dumps(profile_scoring, separators=(",", ":"))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # JavaScript block
    js_block = """
<script>
const REPO = '""" + REPO + """';
const JOBS = """ + jobs_json_str + """;
const PROFILE = """ + profile_json_str + """;
let displayCount = 50;
let expandedId = null;

function scoreColor(s) {
  if (s >= 70) return '#22c55e';
  if (s >= 50) return '#eab308';
  return '#ef4444';
}

function statusDot(s) {
  const m = {open:'\\u{1F7E2}',closed:'\\u{1F534}',applied:'\\u{1F535}',interviewing:'\\u{1F7E3}',offer:'\\u{2B50}',rejected:'\\u{26D4}',withdrawn:'\\u{2B1C}'};
  return m[s] || '\\u{2753}';
}

function pill(text, bg='#1e3a5f', fg='#7dd3fc') {
  return `<span style="display:inline-block;background:${bg};color:${fg};font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;margin:0 3px 3px 0;">${esc(text)}</span>`;
}

function esc(t) {
  const d = document.createElement('div');
  d.textContent = t || '';
  return d.innerHTML;
}

function getFiltered() {
  const q = (document.getElementById('search').value || '').toLowerCase();
  const scoreMin = parseInt(document.getElementById('filter-score').value) || 0;
  const status = document.getElementById('filter-status').value;
  const source = document.getElementById('filter-source').value;
  const sortBy = document.getElementById('sort-by').value;

  let jobs = JOBS.filter(j => {
    if (q && !j.title.toLowerCase().includes(q) && !j.company.toLowerCase().includes(q)) return false;
    if (j.score < scoreMin) return false;
    if (status && (j.tracking ? j.tracking.status : j.status) !== status) return false;
    if (source && j.source !== source) return false;
    return true;
  });

  if (sortBy === 'score-desc') jobs.sort((a,b) => b.score - a.score);
  else if (sortBy === 'score-asc') jobs.sort((a,b) => a.score - b.score);
  else if (sortBy === 'date-desc') jobs.sort((a,b) => b.first_seen.localeCompare(a.first_seen));
  else if (sortBy === 'date-asc') jobs.sort((a,b) => a.first_seen.localeCompare(b.first_seen));
  else if (sortBy === 'company-asc') jobs.sort((a,b) => a.company.localeCompare(b.company));
  else if (sortBy === 'salary-desc') jobs.sort((a,b) => (b.salary_max||0) - (a.salary_max||0));

  return jobs;
}

function render() {
  const jobs = getFiltered();
  const tbody = document.getElementById('job-table-body');
  const showing = Math.min(displayCount, jobs.length);

  document.getElementById('result-count').textContent = `${jobs.length} jobs${showing < jobs.length ? ` (showing ${showing})` : ''}`;

  let html = '';
  for (let i = 0; i < showing; i++) {
    const j = jobs[i];
    const sc = scoreColor(j.score);
    const fill = Math.max(Math.round(j.score / 100 * 140), 6);
    const st = j.tracking ? j.tracking.status : j.status;
    const skills = (j.matched_skills||[]).slice(0,6).map(s => pill(s)).join('');
    const extra = (j.matched_skills||[]).length > 6 ? ` <span style="color:#64748b;font-size:11px;">+${j.matched_skills.length-6}</span>` : '';
    const flags = (j.flags||[]).slice(0,3).map(f => pill(f,'#3b1818','#fca5a5')).join('');
    const sal = j.salary_min && j.salary_max ? `<span style="background:#065f46;color:#6ee7b7;font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;margin-left:8px;">$${Math.round(j.salary_min/1000)}K\\u2013$${Math.round(j.salary_max/1000)}K</span>` : '';

    html += `<tr style="border-bottom:1px solid #1e293b;cursor:pointer;" onclick="toggle('${j.id}')">
      <td style="padding:12px 16px;vertical-align:top;">
        <div><a href="${esc(j.url)}" target="_blank" onclick="event.stopPropagation()" style="color:#60a5fa;font-size:14px;font-weight:600;text-decoration:none;">${esc(j.title)}</a>${sal}</div>
        <div style="color:#94a3b8;font-size:13px;">${esc(j.company)} <span style="color:#64748b;margin-left:8px;">${esc(j.location)}</span></div>
        <div style="margin-top:4px;">${skills}${extra}</div>
        ${flags ? `<div style="margin-top:4px;">${flags}</div>` : ''}
      </td>
      <td style="padding:12px 8px;vertical-align:top;text-align:center;">
        <div style="background:#334155;border-radius:6px;height:18px;width:140px;display:inline-block;vertical-align:middle;">
          <div style="background:${sc};border-radius:6px;height:18px;width:${fill}px;line-height:18px;color:#0f172a;font-size:11px;font-weight:700;padding:0 6px;white-space:nowrap;">${j.score}</div>
        </div>
      </td>
      <td style="padding:12px 8px;vertical-align:top;text-align:center;font-size:12px;color:#94a3b8;">${esc(j.source)}</td>
      <td style="padding:12px 8px;vertical-align:top;text-align:center;font-size:12px;color:#94a3b8;">${statusDot(st)} ${esc(j.first_seen)}</td>
    </tr>`;

    // Expandable detail row
    html += `<tr id="detail-${j.id}" style="display:${expandedId===j.id?'table-row':'none'};border-bottom:1px solid #334155;">
      <td colspan="4" style="padding:0 16px 16px 16px;">
        <div style="background:#0f172a;border:1px solid #334155;border-radius:10px;padding:16px;margin-top:4px;">
          ${renderDetail(j)}
        </div>
      </td>
    </tr>`;
  }

  if (jobs.length > showing) {
    html += `<tr><td colspan="4" style="padding:16px;text-align:center;">
      <button onclick="displayCount+=50;render();" style="background:#1d4ed8;color:#fff;border:none;padding:8px 24px;border-radius:8px;cursor:pointer;font-weight:600;">Show More (${jobs.length - showing} remaining)</button>
    </td></tr>`;
  }

  tbody.innerHTML = html || '<tr><td colspan="4" style="padding:24px;text-align:center;color:#64748b;">No jobs match your filters.</td></tr>';
}

function renderDetail(j) {
  let html = '';

  // Description preview
  if (j.desc_preview) {
    html += `<div style="color:#94a3b8;font-size:13px;margin-bottom:12px;">${esc(j.desc_preview)}${j.desc_preview.length >= 400 ? '...' : ''}</div>`;
  }

  // LLM insights
  if (j.llm) {
    const l = j.llm;
    const recColors = {Apply:'#22c55e',Maybe:'#eab308',Skip:'#ef4444'};
    const recBg = recColors[l.recommendation] || '#64748b';
    html += `<div style="background:#1e293b;border-radius:8px;padding:12px;margin-bottom:12px;border:1px solid #334155;">`;
    html += `<div style="margin-bottom:8px;">
      <span style="background:${recBg};color:#0f172a;font-size:12px;font-weight:700;padding:3px 10px;border-radius:10px;">${esc(l.recommendation||'')}</span>
      <span style="color:#94a3b8;font-size:12px;margin-left:8px;">AI: ${l.llm_score||'?'}/10</span>
      ${l.role_type ? `<span style="color:#64748b;font-size:11px;margin-left:8px;">${esc(l.role_type)}</span>` : ''}
      ${l.team_size ? `<span style="color:#64748b;font-size:11px;margin-left:8px;">Team: ${esc(l.team_size)}</span>` : ''}
      ${l.reports_to ? `<span style="color:#64748b;font-size:11px;margin-left:8px;">Reports to: ${esc(l.reports_to)}</span>` : ''}
    </div>`;
    if (l.strengths) html += l.strengths.map(s => `<div style="color:#4ade80;font-size:12px;">+ ${esc(s)}</div>`).join('');
    if (l.concerns) html += l.concerns.map(c => `<div style="color:#f87171;font-size:12px;">- ${esc(c)}</div>`).join('');
    html += `</div>`;
  }

  // Action buttons
  html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">`;
  const st = j.tracking ? j.tracking.status : j.status;
  const trackBtns = [
    {s:'applied',label:'Mark Applied',bg:'#1d4ed8'},
    {s:'interviewing',label:'Interviewing',bg:'#7c3aed'},
    {s:'offer',label:'Offer',bg:'#059669'},
    {s:'rejected',label:'Rejected',bg:'#dc2626'},
  ];
  html += trackBtns.map(b => {
    const active = st === b.s;
    return `<button onclick="event.stopPropagation();openTrack('${j.id}','${b.s}','${esc(j.title)}','${esc(j.company)}')" style="background:${active?b.bg:'#334155'};color:${active?'#fff':'#94a3b8'};border:1px solid ${active?b.bg:'#475569'};padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600;">${active?'\\u2713 ':''}${b.label}</button>`;
  }).join('');

  // Feedback buttons
  if (j.llm) {
    const hasFb = j.feedback && j.feedback.some(f => f.field === 'recommendation');
    if (hasFb) {
      const fb = j.feedback.find(f => f.field === 'recommendation');
      html += `<span style="color:#64748b;font-size:12px;margin-left:8px;">You rated: ${fb.rating === 'accurate' ? '\\u{1F44D}' : '\\u{1F44E}'} ${fb.rating}</span>`;
    } else {
      html += `<button onclick="event.stopPropagation();openFeedback('${j.id}','recommendation','accurate','${esc(j.title)}','${esc(j.company)}')" style="background:#334155;color:#4ade80;border:1px solid #475569;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;">\\u{1F44D} Good rec</button>`;
      html += `<button onclick="event.stopPropagation();openFeedback('${j.id}','recommendation','inaccurate','${esc(j.title)}','${esc(j.company)}')" style="background:#334155;color:#f87171;border:1px solid #475569;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;">\\u{1F44E} Bad rec</button>`;
    }
  }

  html += `</div>`;

  // Tracking notes
  if (j.tracking && j.tracking.notes) {
    html += `<div style="margin-top:8px;color:#64748b;font-size:11px;">Notes: ${esc(j.tracking.notes)}</div>`;
  }

  return html;
}

function toggle(id) {
  const row = document.getElementById('detail-' + id);
  if (!row) return;
  if (expandedId === id) {
    row.style.display = 'none';
    expandedId = null;
  } else {
    if (expandedId) {
      const prev = document.getElementById('detail-' + expandedId);
      if (prev) prev.style.display = 'none';
    }
    row.style.display = 'table-row';
    expandedId = id;
  }
}

function openTrack(jobId, status, title, company) {
  const issueTitle = `[TRACK] ${jobId} ${status}`;
  const body = `Job: ${title} @ ${company}\\nAction: ${status}\\nTimestamp: ${new Date().toISOString()}`;
  window.open(`https://github.com/${REPO}/issues/new?title=${encodeURIComponent(issueTitle)}&body=${encodeURIComponent(body)}&labels=bot-track`, '_blank');
}

function openFeedback(jobId, field, rating, title, company) {
  const issueTitle = `[FEEDBACK] ${jobId} ${field} ${rating}`;
  const body = `Job: ${title} @ ${company}\\nField: ${field}\\nRating: ${rating}`;
  window.open(`https://github.com/${REPO}/issues/new?title=${encodeURIComponent(issueTitle)}&body=${encodeURIComponent(body)}&labels=bot-feedback`, '_blank');
}

// === Client-side Tier 1 Scoring for Evaluate ===

function hasMatch(text, term) {
  return text.includes(term.toLowerCase());
}

function clientScore(title, company, description) {
  const desc = (description || '').toLowerCase();
  const t = (title || '').toLowerCase();
  const co = (company || '').toLowerCase();
  const allText = desc + ' ' + co;

  // 1. Title match (0-30)
  let titlePts = 0;
  const targets = PROFILE.target_titles.map(x => x.toLowerCase());
  if (targets.some(tt => t.includes(tt) || tt.includes(t))) titlePts = 30;
  else if (t.includes('product') && /director|vp|vice president|head of|senior manager|associate director/.test(t)) titlePts = 20;
  else if (/senior product manager|staff product manager|principal product manager|group product manager|product lead/.test(t)) titlePts = 10;

  // 2. Skill match (0-40)
  const skills = PROFILE.skills || {};
  const variants = PROFILE.skill_variants || {};
  let matched = [], gaps = [];
  let highPts = 0, medPts = 0, lowPts = 0;

  function skillMatch(skill) {
    if (hasMatch(desc, skill)) return true;
    for (const v of (variants[skill] || [])) {
      if (hasMatch(desc, v)) return true;
    }
    return false;
  }

  for (const s of (skills.high || [])) {
    if (skillMatch(s)) { matched.push(s); highPts += 3; } else gaps.push(s);
  }
  highPts = Math.min(highPts, 24);
  for (const s of (skills.medium || [])) {
    if (skillMatch(s)) { matched.push(s); medPts += 2; } else gaps.push(s);
  }
  medPts = Math.min(medPts, 10);
  for (const s of (skills.low || [])) {
    if (skillMatch(s)) { matched.push(s); lowPts += 1; } else gaps.push(s);
  }
  lowPts = Math.min(lowPts, 6);
  const skillPts = highPts + medPts + lowPts;

  // 3. Experience alignment (0-15)
  let expPts = 0;
  const myYears = PROFILE.total_years || 15;
  const yearsMatch = desc.match(/(\d{1,2})\s*[\-–to]+\s*(\d{1,2})\s*\+?\s*year/i) ||
                     desc.match(/(\d{1,2})\s*\+\s*year/i);
  if (yearsMatch) {
    const lo = parseInt(yearsMatch[1]);
    const hi = yearsMatch[2] ? parseInt(yearsMatch[2]) : lo + 10;
    if (myYears >= lo && myYears <= hi) expPts += 10;
  } else {
    expPts += 5; // no years mentioned = neutral credit
  }
  const teamKw = ['manage a team','managing a team','lead a team','leading a team','direct reports','people management','team of','org of'];
  if (teamKw.some(k => desc.includes(k))) expPts += 5;

  // 4. Industry/company fit (0-15)
  let indPts = 0;
  const industries = (PROFILE.industries || []).map(i => i.toLowerCase());
  let indMatches = 0;
  for (const ind of industries) {
    if (hasMatch(allText, ind)) indMatches++;
  }
  indPts += Math.min(indMatches * 5, 10);
  const entKw = ['enterprise','fortune 500','large-scale','global','publicly traded'];
  const midKw = ['series b','series c','series d','growth stage','mid-size'];
  const sizes = PROFILE.company_sizes || [];
  if (sizes.includes('enterprise') && entKw.some(k => hasMatch(allText, k))) indPts += 5;
  if (sizes.includes('mid-size') && midKw.some(k => hasMatch(allText, k))) indPts += 5;
  indPts = Math.min(indPts, 15);

  // 5. Penalties
  let penalty = 0;
  let flags = [];
  if (/\b(hands[- ]on\s+coding|write\s+production\s+code|must\s+code\s+daily)\b/i.test(desc)) {
    penalty -= 15; flags.push('dealbreaker: hands-on coding');
  } else if (/\b(junior|entry[- ]level)\b/i.test(t + ' ' + desc)) {
    penalty -= 15; flags.push('dealbreaker: junior scope');
  }
  const overqualMatch = desc.match(/\b([1-5])\s*[\-–to]+\s*([3-7])\s*\+?\s*year/i);
  if (overqualMatch) {
    const hi = parseInt(overqualMatch[2]);
    if (myYears > hi + 5) { penalty -= 10; flags.push('overqualified'); }
  }

  // 6. Boosts
  const companyBoost = PROFILE.target_companies.includes(co) ? 5 : 0;
  const descBoost = desc.length >= 500 ? 3 : 0;

  const raw = titlePts + skillPts + expPts + indPts + penalty + companyBoost + descBoost;
  const score = Math.max(raw, 0);

  return { score, titlePts, skillPts, expPts, indPts, penalty, companyBoost, descBoost, matched, gaps, flags };
}

function runEvaluate() {
  const title = document.getElementById('eval-title').value.trim();
  const company = document.getElementById('eval-company').value.trim();
  const location = document.getElementById('eval-location').value.trim();
  const description = document.getElementById('eval-description').value.trim();

  if (!description) { alert('Please paste a job description'); return; }

  const r = clientScore(title, company, description);
  const sc = scoreColor(r.score);

  let html = `<div style="padding:16px;">`;
  html += `<div style="font-size:20px;font-weight:800;color:${sc};margin-bottom:8px;">Tier 1 Score: ${r.score}</div>`;
  html += `<div style="font-size:13px;color:#94a3b8;margin-bottom:12px;">`;
  html += `Title: ${r.titlePts}/30 &middot; Skills: ${r.skillPts}/40 &middot; Experience: ${r.expPts}/15 &middot; Industry: ${r.indPts}/15 &middot; Company: +${r.companyBoost} &middot; Desc: +${r.descBoost}`;
  if (r.penalty) html += ` &middot; <span style="color:#ef4444;">Penalty: ${r.penalty}</span>`;
  html += `</div>`;
  if (r.flags.length) {
    html += `<div style="margin-bottom:8px;">`;
    html += r.flags.map(f => pill(f, '#3b1818', '#fca5a5')).join('');
    html += `</div>`;
  }
  html += `<div style="margin-bottom:8px;"><span style="color:#e2e8f0;font-size:13px;font-weight:600;">Matched (${r.matched.length}):</span> `;
  html += r.matched.map(s => pill(s)).join('');
  html += `</div>`;
  html += `<div style="margin-bottom:12px;"><span style="color:#e2e8f0;font-size:13px;font-weight:600;">Gaps (${r.gaps.length}):</span> `;
  html += r.gaps.slice(0, 8).map(s => pill(s, '#3b1818', '#fca5a5')).join('');
  if (r.gaps.length > 8) html += ` <span style="color:#64748b;font-size:11px;">+${r.gaps.length-8}</span>`;
  html += `</div>`;

  // Deep evaluate button (Tier 2 via GitHub Issue)
  html += `<button onclick="submitDeepEval()" style="background:#7c3aed;color:#fff;border:none;padding:8px 20px;border-radius:8px;cursor:pointer;font-weight:600;font-size:13px;">Deep Evaluate with AI (Tier 2)</button>`;
  html += `<span style="color:#64748b;font-size:11px;margin-left:8px;">Opens GitHub Issue — results posted as comment in ~60s</span>`;
  html += `</div>`;

  document.getElementById('eval-result').innerHTML = html;
  document.getElementById('eval-result').style.display = 'block';
}

function submitDeepEval() {
  const title = document.getElementById('eval-title').value.trim() || 'Unknown';
  const company = document.getElementById('eval-company').value.trim() || 'Unknown';
  const location = document.getElementById('eval-location').value.trim() || '';
  const description = document.getElementById('eval-description').value.trim();

  const issueTitle = '[EVALUATE] Manual job evaluation';
  const body = `Title: ${title}\\nCompany: ${company}\\nLocation: ${location}\\nDescription:\\n${description.substring(0, 3000)}`;
  const url = `https://github.com/${REPO}/issues/new?title=${encodeURIComponent(issueTitle)}&body=${encodeURIComponent(body)}&labels=bot-evaluate`;
  window.open(url, '_blank');
}

// Init
document.addEventListener('DOMContentLoaded', () => {
  // Populate source dropdown
  const sources = [...new Set(JOBS.map(j => j.source))].sort();
  const sel = document.getElementById('filter-source');
  sources.forEach(s => { const o = document.createElement('option'); o.value = s; o.textContent = s; sel.appendChild(o); });

  // Wire up filters
  ['search','filter-score','filter-status','filter-source','sort-by'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener(id === 'search' ? 'input' : 'change', () => { displayCount = 50; expandedId = null; render(); });
  });

  render();
});
</script>"""

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
      color: #94a3b8; font-weight: 600; border-bottom: 2px solid #334155;
      cursor: default; }}
    .footer {{ text-align: center; padding: 24px 0; color: #64748b; font-size: 12px; }}
    a {{ color: #60a5fa; text-decoration: none; }}
    a:hover {{ text-decoration: underline; color: #93c5fd; }}
    .filter-bar {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }}
    .filter-bar input, .filter-bar select {{
      background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
      border-radius: 6px; padding: 6px 10px; font-size: 13px; }}
    .filter-bar input:focus, .filter-bar select:focus {{ outline: none; border-color: #3b82f6; }}
    .filter-bar input {{ width: 200px; }}
  </style>
</head>
<body>
  <div class="wrap">

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
        {fb_card}
      </div>
    </div>

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

    <div class="card">
      <h2>All Jobs (7d)</h2>
      <div class="filter-bar">
        <input type="text" id="search" placeholder="Search title or company...">
        <select id="filter-score">
          <option value="0">All Scores</option>
          <option value="90">90+ Excellent</option>
          <option value="70" selected>70+ Strong</option>
          <option value="50">50+ Moderate</option>
          <option value="30">30+ Weak</option>
        </select>
        <select id="filter-status">
          <option value="">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="applied">Applied</option>
          <option value="interviewing">Interviewing</option>
          <option value="offer">Offer</option>
          <option value="rejected">Rejected</option>
        </select>
        <select id="filter-source"><option value="">All Sources</option></select>
        <select id="sort-by">
          <option value="score-desc">Score (High to Low)</option>
          <option value="score-asc">Score (Low to High)</option>
          <option value="date-desc">Newest First</option>
          <option value="date-asc">Oldest First</option>
          <option value="company-asc">Company A-Z</option>
          <option value="salary-desc">Salary (High to Low)</option>
        </select>
        <span id="result-count" style="color:#94a3b8;font-size:12px;"></span>
      </div>
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
          <tbody id="job-table-body"></tbody>
        </table>
      </div>
      <div style="padding:12px 24px;border-top:1px solid #334155;font-size:12px;color:#64748b;display:flex;gap:16px;flex-wrap:wrap;">
        <span style="font-weight:600;">Legend:</span>
        <span>&#128994; Open</span>
        <span>&#128308; Closed</span>
        <span>&#128309; Applied</span>
        <span>&#128995; Interviewing</span>
        <span>&#11088; Offer</span>
        <span>&#9940; Rejected</span>
        <span style="margin-left:12px;">Click any row to expand details and track status</span>
      </div>
    </div>

    <!-- Evaluate a Job -->
    <div class="card">
      <h2>Evaluate a Job</h2>
      <p style="color:#94a3b8;font-size:13px;margin-bottom:14px;">
        Paste a job description to get an instant Tier 1 score, or click "Deep Evaluate" for Claude AI analysis.
      </p>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:10px;">
        <input type="text" id="eval-title" placeholder="Job title" style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:8px 10px;font-size:13px;">
        <input type="text" id="eval-company" placeholder="Company" style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:8px 10px;font-size:13px;">
        <input type="text" id="eval-location" placeholder="Location" style="background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:8px 10px;font-size:13px;">
      </div>
      <textarea id="eval-description" rows="8" placeholder="Paste the full job description here..."
        style="width:100%;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:10px;font-size:13px;font-family:inherit;resize:vertical;margin-bottom:10px;"></textarea>
      <button onclick="runEvaluate()" style="background:#1d4ed8;color:#fff;border:none;padding:10px 24px;border-radius:8px;cursor:pointer;font-weight:600;font-size:14px;">Score This Job</button>
      <div id="eval-result" style="display:none;margin-top:14px;background:#0f172a;border:1px solid #334155;border-radius:10px;"></div>
    </div>

    <div class="footer">
      Winters Job Finder &middot; Generated {generated_at} &middot;
      <a href="https://github.com/{REPO}">GitHub</a>
    </div>

  </div>
  {js_block}
</body>
</html>"""


def build(db_path: str | Path | None = None) -> Path:
    DOCS_DIR.mkdir(exist_ok=True)
    conn = db.init_db(db_path)
    html = generate_dashboard(conn)
    conn.close()
    OUTPUT_FILE.write_text(html)
    print(f"Dashboard written to {OUTPUT_FILE}", file=sys.stderr)
    return OUTPUT_FILE


if __name__ == "__main__":
    build()
