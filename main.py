"""Winters Job Finder — main pipeline orchestrator.

Steps:
  1. Load candidate profile (env var PROFILE_JSON or local profile.json)
  2. Run all collectors (adzuna, career_pages, remotive)
  3. Deduplicate against the SQLite database
  4. Filter by location (remote or Seattle metro)
  5. Score new jobs with scorer.py
  6. (Optional) LLM re-score jobs above 50 if USE_LLM_SCORING=true
  7. Build HTML email digest
  8. Send email via Brevo SMTP
  9. Persist all jobs to the database
 10. Print summary

Usage:
    python main.py
    python -m main

Environment variables:
    PROFILE_JSON        — JSON string of the profile (overrides profile.json)
    USE_LLM_SCORING     — set to 'true' to enable LLM re-scoring (stub)
    ADZUNA_APP_ID       — Adzuna API credentials
    ADZUNA_API_KEY
    BREVO_SMTP_KEY      — Brevo SMTP key
    EMAIL_TO            — digest recipient(s)
    SKIP_EMAIL          — set to 'true' to skip sending (useful for testing)
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

MAX_JOB_AGE_DAYS = 30

import db
from digest import generate_digest
from emailer import send_digest
from scorer import load_profile, score_jobs

# ---------------------------------------------------------------------------
# Collector registry — each must expose a search_jobs() -> list[dict]
# ---------------------------------------------------------------------------

_COLLECTORS: list[tuple[str, str]] = [
    ("adzuna", "collectors.adzuna"),
    ("career_pages", "collectors.career_pages"),
    ("remotive", "collectors.remotive"),
    ("hn_hiring", "collectors.hn_hiring"),
    ("gov_jobs", "collectors.gov_jobs"),
]

# Target location allowlist — specific cities/regions we want
_LOCATION_RE = re.compile(
    r"\b(seattle|bellevue|redmond|kirkland|tacoma|renton|kent|bothell"
    r"|woodinville|issaquah|sammamish|mercer\s+island"
    r"|whidbey|oak\s+harbor|everett"
    r"|pittsburgh"
    r"|victoria\s*,?\s*(?:b\.?c\.?|british\s+columbia)"
    r"|british\s+columbia"
    r"|island\s+county|skagit\s+county|snohomish\s+county"
    r"|oak\s+harbor|anacortes|coupeville|langley|freeland"
    r"|mount\s+vernon|burlington|sedro[- ]woolley|la\s+conner"
    r"|marysville|lake\s+stevens|snohomish|stanwood|arlington"
    r"|lynnwood|edmonds|mukilteo|mountlake\s+terrace"
    r")\b",
    re.I,
)

# US-qualified remote — "remote" alone is NOT enough (could be remote in Europe)
_REMOTE_US_RE = re.compile(
    r"(?:remote|work\s+from\s+home|distributed)"
    r"[^.]{0,40}"
    r"(?:u\.?s\.?a?\.?|united\s+states|america)",
    re.I,
)
_REMOTE_US_RE2 = re.compile(
    r"(?:u\.?s\.?a?\.?|united\s+states)"
    r"[^.]{0,40}"
    r"(?:remote|work\s+from\s+home)",
    re.I,
)

# Hard blocklist — reject jobs in these countries/cities even if they mention "Remote"
_BLOCKED_LOCATIONS_RE = re.compile(
    r"\b("
    # Countries
    r"india|ireland|germany|united\s+kingdom|france|spain|"
    r"netherlands|australia|singapore|japan|china|brazil|mexico|israel|"
    r"poland|czech|romania|bulgaria|ukraine|turkey|philippines|"
    r"argentina|chile|colombia|nigeria|kenya|south\s+africa|"
    r"new\s+zealand|sweden|norway|denmark|finland|belgium|austria|"
    r"switzerland|portugal|italy|greece|hungary|croatia|"
    r"canada(?!\s*,?\s*(?:bc|b\.c\.|british\s+columbia|victoria))"
    r"|"
    # Non-target cities
    r"london|dublin|berlin|munich|amsterdam|paris|barcelona|madrid|"
    r"bangalore|bengaluru|hyderabad|pune|mumbai|chennai|delhi|noida|"
    r"gurgaon|gurugram|kolkata|"
    r"sydney|melbourne|brisbane|"
    r"tokyo|osaka|shanghai|beijing|shenzhen|"
    r"hong\s+kong|taipei|seoul|"
    r"tel\s+aviv|haifa|"
    r"prague|warsaw|bucharest|sofia|krakow|wroclaw|"
    r"stockholm|oslo|copenhagen|helsinki|"
    r"toronto|montreal|vancouver(?!\s*island)"
    r")\b",
    re.I,
)

# Negative keywords — skip jobs containing these terms in title or description
_NEGATIVE_RE = re.compile(
    r"\b("
    r"intern\b|internship|part[- ]time|clearance\s+required|"
    r"security\s+clearance|ts/sci|polygraph|"
    r"on[- ]site\s+only|no\s+remote|"
    r"unpaid|volunteer|equity[- ]only"
    r")\b",
    re.I,
)

# Title-only exclusions — reject roles whose TITLE matches a marketing-family
# pattern. Applied to the job title only (not description) to avoid false
# positives from JDs that mention "product marketing team" in passing.
# Carefully crafted so "Product Manager, Marketing Technology" still passes:
# the marketing patterns require adjacency ("product marketing" with only
# whitespace between), so "Product Manager, Marketing ..." does not match.
_EXCLUDED_TITLE_RE = re.compile(
    r"\b("
    r"product\s+marketing|pmm|"
    r"growth\s+marketing|demand\s+gen(?:eration)?|"
    r"brand\s+marketing|field\s+marketing|content\s+marketing|"
    r"marketing\s+manager|marketing\s+director|"
    r"head\s+of\s+marketing|vp\s+(?:of\s+)?marketing|"
    r"chief\s+marketing\s+officer|cmo"
    r")\b",
    re.I,
)


# ---------------------------------------------------------------------------
# 1. Profile loading
# ---------------------------------------------------------------------------

def _load_profile() -> dict[str, Any]:
    env_json = os.environ.get("PROFILE_JSON", "").strip()
    if env_json:
        print("Loading profile from PROFILE_JSON env var", file=sys.stderr)
        return json.loads(env_json)
    print("Loading profile from profile.json", file=sys.stderr)
    return load_profile()


# ---------------------------------------------------------------------------
# 2. Collect jobs from all sources
# ---------------------------------------------------------------------------

def _collect_all() -> list[dict[str, Any]]:
    import importlib

    all_jobs: list[dict[str, Any]] = []
    for name, module_path in _COLLECTORS:
        print(f"\n>>> Collector: {name}", file=sys.stderr)
        try:
            mod = importlib.import_module(module_path)
            jobs = mod.search_jobs()
            print(f"    {name}: {len(jobs)} jobs", file=sys.stderr)
            all_jobs.extend(jobs)
        except Exception as exc:
            print(f"    {name}: FAILED — {exc}", file=sys.stderr)
    return all_jobs


# ---------------------------------------------------------------------------
# 3. Deduplicate
# ---------------------------------------------------------------------------

def _deduplicate(
    jobs: list[dict[str, Any]],
    conn: Any,
) -> list[dict[str, Any]]:
    new: list[dict[str, Any]] = []
    for job in jobs:
        jid = db.job_id(
            job.get("title", ""),
            job.get("company", ""),
            job.get("url", ""),
        )
        if not db.is_seen(conn, jid):
            new.append(job)
    return new


def _dedupe_by_title_company(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse jobs with the same title+company (different URL variants).

    Greenhouse often posts one role with multiple location-specific URLs.
    Keep the first occurrence and drop the rest.
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in jobs:
        key = f"{job.get('title', '').lower().strip()}|{job.get('company', '').lower().strip()}"
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


# ---------------------------------------------------------------------------
# 4. Location filter
# ---------------------------------------------------------------------------

def _is_recent(job: dict[str, Any]) -> bool:
    """Return True if the job was posted within MAX_JOB_AGE_DAYS, or has no date."""
    dp = job.get("date_posted", "")
    if not dp:
        return True  # no date = assume current
    try:
        # Handle various ISO formats
        clean = dp.replace("Z", "+00:00")
        if "T" in clean:
            posted = datetime.fromisoformat(clean)
        else:
            posted = datetime.strptime(clean[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_JOB_AGE_DAYS)
        return posted >= cutoff
    except (ValueError, TypeError):
        return True  # unparseable date = keep it


def _passes_negative_filter(job: dict[str, Any]) -> bool:
    """Return False if the job contains negative keywords (intern, part-time, etc.)."""
    text = f"{job.get('title', '')} {(job.get('description', '') or '')[:500]}"
    return not _NEGATIVE_RE.search(text)


def _passes_title_exclusion(job: dict[str, Any]) -> bool:
    """Return False if the job title is a marketing-family role.

    Title-only match (not description) to avoid false positives from product
    JDs that reference "the product marketing team" in passing.
    """
    return not _EXCLUDED_TITLE_RE.search(job.get("title", ""))


def _matches_location(job: dict[str, Any]) -> bool:
    location = job.get("location", "")
    text = " ".join([
        location,
        job.get("title", ""),
        (job.get("description", "") or "")[:500],
    ])

    # 1. Check allowlist FIRST — if a target city is in the location, accept
    if _LOCATION_RE.search(location):
        return True

    # 2. HARD BLOCK: reject if location field contains non-target country/city
    if location and _BLOCKED_LOCATIONS_RE.search(location):
        return False

    # 3. Check allowlist on full text (title + description)
    if _LOCATION_RE.search(text):
        return True

    # 4. US-qualified remote (must mention US/USA/United States near "remote")
    if _REMOTE_US_RE.search(text) or _REMOTE_US_RE2.search(text):
        return True

    return False


# ---------------------------------------------------------------------------
# 6. LLM scoring stub
# ---------------------------------------------------------------------------

def _llm_rescore(jobs: list[dict[str, Any]], profile: dict[str, Any],
                  conn: Any = None) -> list[dict[str, Any]]:
    """Run LLM re-scoring on jobs with Tier 1 score >= 60."""
    try:
        from scorer_llm import score_jobs_llm
        return score_jobs_llm(jobs, profile, threshold=60, db_conn=conn)
    except Exception as exc:
        print(f"LLM re-scoring failed: {exc}", file=sys.stderr)
        return jobs


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _regenerate_dashboard(conn: Any) -> None:
    """Regenerate docs/index.html from current DB state. Safe to call anytime."""
    try:
        from dashboard import generate_dashboard
        from pathlib import Path
        docs_dir = Path(__file__).resolve().parent / "docs"
        docs_dir.mkdir(exist_ok=True)
        html = generate_dashboard(conn)
        (docs_dir / "index.html").write_text(html)
        print(f"Dashboard written to docs/index.html", file=sys.stderr)
    except Exception as exc:
        print(f"Dashboard generation failed: {exc}", file=sys.stderr)


def run() -> None:
    """Execute the full pipeline."""

    # 1. Profile
    profile = _load_profile()

    # 2. Collect
    raw_jobs = _collect_all()
    print(f"\nCollected {len(raw_jobs)} total jobs", file=sys.stderr)

    if not raw_jobs:
        print("No jobs collected — regenerating dashboard and exiting.", file=sys.stderr)
        conn = db.init_db()
        _regenerate_dashboard(conn)
        conn.close()
        return

    # 2b. Filter stale postings (>30 days old)
    raw_jobs = [j for j in raw_jobs if _is_recent(j)]
    print(f"After age filter (≤{MAX_JOB_AGE_DAYS}d): {len(raw_jobs)}", file=sys.stderr)

    # 2c. Normalize titles (Sr. PM → Senior Product Manager, etc.)
    from db import _normalize_title
    for j in raw_jobs:
        j["title"] = _normalize_title(j.get("title", ""))

    # 2d. Collapse same title+company (Greenhouse multi-location dupes)
    raw_jobs = _dedupe_by_title_company(raw_jobs)
    print(f"After title+company dedup: {len(raw_jobs)}", file=sys.stderr)

    # 3. Deduplicate against DB
    conn = db.init_db()
    new_jobs = _deduplicate(raw_jobs, conn)
    print(f"New (unseen) jobs: {len(new_jobs)}", file=sys.stderr)

    # 4. Location filter
    filtered = [j for j in new_jobs if _matches_location(j)]
    print(f"After location filter: {len(filtered)}", file=sys.stderr)

    # 4b. Negative keyword filter
    filtered = [j for j in filtered if _passes_negative_filter(j)]
    print(f"After negative keyword filter: {len(filtered)}", file=sys.stderr)

    # 4c. Title exclusion filter (marketing-family roles)
    filtered = [j for j in filtered if _passes_title_exclusion(j)]
    print(f"After title exclusion filter: {len(filtered)}", file=sys.stderr)

    if not filtered:
        print("No new jobs after filtering — saving raw, regenerating dashboard, and exiting.", file=sys.stderr)
        db.save_jobs(conn, raw_jobs)
        _regenerate_dashboard(conn)
        conn.close()
        return

    # 5. Score
    scored = score_jobs(filtered, profile)
    print(f"Scored {len(scored)} jobs", file=sys.stderr)

    # 5b. Backfill: re-score any DB jobs that have NULL/0 scores
    unscored = db.get_unscored_jobs(conn)
    if unscored:
        print(f"Backfill scoring {len(unscored)} unscored DB jobs …", file=sys.stderr)
        from scorer import score_job, _load_target_companies
        profile_with_boost = {**profile, "_target_companies": _load_target_companies()}
        backfilled = 0
        for uj in unscored:
            if not uj.get("description"):
                continue
            result = score_job(uj, profile_with_boost)
            jid = db.job_id(uj.get("title", ""), uj.get("company", ""), uj.get("url", ""))
            db.update_score(
                conn, jid, result["score"],
                result["matched_skills"], result["flags"],
            )
            backfilled += 1
        print(f"  Backfilled {backfilled} jobs", file=sys.stderr)

    # 6. Optional LLM re-scoring
    if os.environ.get("USE_LLM_SCORING", "").lower() == "true":
        scored = _llm_rescore(scored, profile, conn=conn)

    # 7. Build digest (with still-open, long-open, and recently-closed sections)
    today = date.today()
    still_open = db.get_open_jobs(conn, days=7)
    recently_closed = db.get_closed_jobs(conn, days=3)
    long_open = db.get_long_open_jobs(conn, min_days=7, max_days=30)
    # Exclude today's new jobs from still-open (they're in the main section)
    new_urls = {j.get("url") for j in scored}
    still_open = [j for j in still_open if j.get("url") not in new_urls]
    html_body = generate_digest(
        scored, run_date=today,
        still_open=still_open, recently_closed=recently_closed,
        long_open=long_open,
    )

    # 8. Send email
    skip_email = os.environ.get("SKIP_EMAIL", "").lower() == "true"
    if skip_email:
        print("SKIP_EMAIL=true — skipping email send", file=sys.stderr)
    else:
        try:
            send_digest(html_body, job_count=len(scored), run_date=today)
        except Exception as exc:
            print(f"Email send failed: {exc}", file=sys.stderr)

    # 9. Save all jobs (raw + scored) to DB
    #    scored jobs have _score; raw jobs that didn't pass filters still get saved
    #    so we don't re-process them next run.
    saved_new = db.save_jobs(conn, raw_jobs)
    # Update scores for the filtered/scored subset
    db.save_jobs(conn, scored)

    # 9a2. Save company intel from career pages
    try:
        from collectors.career_pages import get_company_intel
        for co_name, intel in get_company_intel().items():
            db.save_company_intel(conn, co_name, intel["total_roles"], intel["product_roles"])
    except Exception:
        pass

    # 9a3. Log companies from Adzuna not in our target list (for future expansion)
    try:
        from scorer import _load_target_companies
        known = _load_target_companies()
        new_companies = set()
        for j in raw_jobs:
            co = j.get("company", "").strip()
            if co and co.lower() not in known and j.get("source") == "adzuna":
                new_companies.add(co)
        if new_companies:
            print(f"  Discovered {len(new_companies)} new companies from Adzuna (not in target list)", file=sys.stderr)
    except Exception:
        pass

    # 9b. Detect closed jobs (no longer in current collection)
    current_keys = {
        f"{j.get('title', '').lower().strip()}|{j.get('company', '').lower().strip()}"
        for j in raw_jobs
    }
    closed_count = db.mark_closed(conn, current_keys)
    if closed_count:
        print(f"Marked {closed_count} jobs as closed", file=sys.stderr)

    # 9c. Generate dashboard
    _regenerate_dashboard(conn)

    conn.close()

    # 10. Summary
    top_score = scored[0]["_score"]["score"] if scored else 0
    strong = sum(1 for j in scored if j["_score"]["score"] >= 70)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Pipeline complete — {today.isoformat()}", file=sys.stderr)
    print(f"  Collected:    {len(raw_jobs)}", file=sys.stderr)
    print(f"  New:          {len(new_jobs)}", file=sys.stderr)
    print(f"  After filter: {len(filtered)}", file=sys.stderr)
    print(f"  Scored:       {len(scored)}", file=sys.stderr)
    print(f"  Top score:    {top_score}", file=sys.stderr)
    print(f"  Strong (70+): {strong}", file=sys.stderr)
    print(f"  Saved to DB:  {saved_new} new rows", file=sys.stderr)
    print(f"  Email sent:   {'no' if skip_email else 'yes'}", file=sys.stderr)
    print(f"{'=' * 50}", file=sys.stderr)


if __name__ == "__main__":
    import traceback
    try:
        run()
    except Exception as exc:
        # Don't let uncaught exceptions prevent the dashboard commit step.
        # Regenerate the dashboard from whatever DB state exists, then exit 0
        # so the workflow can still commit + deploy the latest docs/.
        print(f"\nPIPELINE ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        try:
            conn = db.init_db()
            _regenerate_dashboard(conn)
            conn.close()
        except Exception as dash_exc:
            print(f"Dashboard fallback also failed: {dash_exc}", file=sys.stderr)
        # Exit 0 so commit step runs. CI still surfaces the error in the logs.
        sys.exit(0)
