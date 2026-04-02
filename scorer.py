"""Score job postings against a candidate profile.

Scoring breakdown (0–100 before penalties):
  - Title match:              0–30 pts
  - Skill match:              0–40 pts  (high 3ea/cap24, med 2ea/cap10, low 1ea/cap6)
  - Experience alignment:     0–15 pts  (years 10, team size 5)
  - Company / industry fit:   0–15 pts  (industry 10, company size 5)
  - Penalties:  dealbreaker –15, overqualified –10

Usage:
    python scorer.py                    # runs built-in demo
    python -m scorer                    # same thing
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROFILE_PATH = Path(__file__).resolve().parent / "profile.json"


def load_profile(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else _PROFILE_PATH
    with open(p) as f:
        return json.load(f)


def _lower(text: str | None) -> str:
    return (text or "").lower()


def _has(haystack: str, needle: str) -> bool:
    """Case-insensitive substring match with word-boundary awareness."""
    return bool(re.search(re.escape(needle), haystack, re.IGNORECASE))


# ---------------------------------------------------------------------------
# 1. Title match  (0–30)
# ---------------------------------------------------------------------------

# Patterns that count as "adjacent" product-leadership titles
_ADJACENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsenior\s+(product\s+manager|pm)\b", re.I),
    re.compile(r"\bstaff\s+product\s+manager\b", re.I),
    re.compile(r"\bprincipal\s+(product\s+manager|pm)\b", re.I),
    re.compile(r"\bgroup\s+product\s+manager\b", re.I),
    re.compile(r"\bproduct\s+lead\b", re.I),
]

# Tokens that help detect a "partial" title match
_TITLE_TOKENS = [
    "director", "vp", "vice president", "head of product",
    "senior manager", "associate director",
]


def _score_title(job_title: str, target_titles: list[str]) -> int:
    jt = _lower(job_title)

    # Exact match (case-insensitive)
    for t in target_titles:
        if _lower(t) == jt or _lower(t) in jt or jt in _lower(t):
            return 30

    # Partial: title contains key leadership + product tokens
    has_product = bool(re.search(r"\bproduct\b", jt))
    has_leadership = any(tok in jt for tok in _TITLE_TOKENS)
    if has_product and has_leadership:
        return 20

    # Adjacent titles (senior PM, group PM, etc.)
    for pat in _ADJACENT_PATTERNS:
        if pat.search(jt):
            return 10

    return 0


# ---------------------------------------------------------------------------
# 2. Skill match  (0–40)
# ---------------------------------------------------------------------------

_HIGH_CAP = 24   # 3 pts × 8 matches max
_MED_CAP = 10    # 2 pts × 5 matches max
_LOW_CAP = 6     # 1 pt  × 6 matches max


def _skill_matches(desc: str, skill: str, variants: dict[str, list[str]]) -> bool:
    """Check if *skill* or any of its variants appear in *desc*."""
    if _has(desc, skill):
        return True
    for v in variants.get(skill, []):
        if _has(desc, v):
            return True
    return False


def _score_skills(
    description: str,
    skills: dict[str, list[str]],
    variants: dict[str, list[str]] | None = None,
) -> tuple[int, list[str], list[str]]:
    desc = _lower(description)
    matched: list[str] = []
    missed: list[str] = []
    vmap = variants or {}

    high_pts = 0
    for s in skills.get("high", []):
        if _skill_matches(desc, s, vmap):
            matched.append(s)
            high_pts += 3
        else:
            missed.append(s)
    high_pts = min(high_pts, _HIGH_CAP)

    med_pts = 0
    for s in skills.get("medium", []):
        if _skill_matches(desc, s, vmap):
            matched.append(s)
            med_pts += 2
        else:
            missed.append(s)
    med_pts = min(med_pts, _MED_CAP)

    low_pts = 0
    for s in skills.get("low", []):
        if _skill_matches(desc, s, vmap):
            matched.append(s)
            low_pts += 1
        else:
            missed.append(s)
    low_pts = min(low_pts, _LOW_CAP)

    return high_pts + med_pts + low_pts, matched, missed


# ---------------------------------------------------------------------------
# 3. Experience alignment  (0–15)
# ---------------------------------------------------------------------------

# Matches patterns like "10+ years", "8-12 years", "15 years"
_YEARS_RE = re.compile(
    r"(\d{1,2})\s*[\-–to]+\s*(\d{1,2})\s*\+?\s*year"
    r"|(\d{1,2})\s*\+\s*year",
    re.I,
)

_TEAM_KEYWORDS = [
    "manage a team", "managing a team", "lead a team", "leading a team",
    "direct reports", "manage reports", "people management",
    "team of", "org of",
]


def _score_experience(
    description: str,
    experience: dict[str, Any],
) -> tuple[int, list[str]]:
    desc = _lower(description)
    flags: list[str] = []
    pts = 0

    my_years: int = experience.get("total_years", 0)

    m = _YEARS_RE.search(description)
    if m:
        if m.group(1) and m.group(2):
            low, high = int(m.group(1)), int(m.group(2))
        elif m.group(3):
            low = int(m.group(3))
            high = low + 10  # "X+ years" → treat as open-ended
        else:
            low, high = 0, 99

        if low <= my_years <= high:
            pts += 10
        elif my_years > high:
            flags.append(f"overqualified-years: JD asks {low}-{high}yr, you have {my_years}")
        # If under-qualified, simply 0 pts (no flag for this profile)
    else:
        pts += 5  # No years mentioned → neutral, slight credit

    # Team size
    for kw in _TEAM_KEYWORDS:
        if kw in desc:
            pts += 5
            break

    return pts, flags


# ---------------------------------------------------------------------------
# 4. Company / industry fit  (0–15)
# ---------------------------------------------------------------------------

_SIZE_ENTERPRISE = [
    "enterprise", "fortune 500", "fortune500", "large-scale", "global",
    "publicly traded", "ipo",
]
_SIZE_MID = ["series b", "series c", "series d", "growth stage", "mid-size"]


def _score_industry(
    description: str,
    company: str,
    experience: dict[str, Any],
    preferences: dict[str, Any],
) -> int:
    desc = _lower(description + " " + company)
    pts = 0

    # Industry overlap
    industries: list[str] = experience.get("industries", [])
    matches = sum(1 for ind in industries if _has(desc, ind))
    pts += min(matches * 5, 10)

    # Company size signals
    target_sizes: list[str] = preferences.get("company_size", [])
    if "enterprise" in target_sizes:
        if any(kw in desc for kw in _SIZE_ENTERPRISE):
            pts += 5
    if "mid-size" in target_sizes:
        if any(kw in desc for kw in _SIZE_MID):
            pts += 5

    return min(pts, 15)


# ---------------------------------------------------------------------------
# 5. Penalties
# ---------------------------------------------------------------------------

_DEALBREAKER_PATTERNS: dict[str, re.Pattern[str]] = {
    "requires hands-on coding as primary function": re.compile(
        r"\b(hands[- ]on\s+coding|write\s+production\s+code|software\s+engineer"
        r"|must\s+code\s+daily)\b", re.I,
    ),
    "requires CS degree": re.compile(
        r"\b(requires?\s+(a\s+)?((BS|bachelor'?s?)\s+(in\s+)?"
        r"computer\s+science|CS\s+degree))\b", re.I,
    ),
    "junior scope": re.compile(
        r"\b(junior|entry[- ]level|associate\s+product\s+manager|apm\s+program)\b", re.I,
    ),
    "contract under 6 months": re.compile(
        r"\b(contract|temp)\b.*?\b([1-5]\s+month|short[- ]term)\b", re.I,
    ),
    "unpaid or equity-only compensation": re.compile(
        r"\b(unpaid|equity[- ]only|no\s+salary)\b", re.I,
    ),
}

_OVERQUALIFIED_RE = re.compile(
    r"\b([1-5])\s*[\-–to]+\s*([3-7])\s*\+?\s*year", re.I,
)


def _score_penalties(
    description: str,
    title: str,
    dealbreakers: list[str],
    experience: dict[str, Any],
) -> tuple[int, list[str]]:
    text = _lower(description + " " + title)
    penalty = 0
    flags: list[str] = []

    for label, pattern in _DEALBREAKER_PATTERNS.items():
        if pattern.search(text):
            penalty -= 15
            flags.append(f"dealbreaker: {label}")
            break  # one dealbreaker is enough to flag

    m = _OVERQUALIFIED_RE.search(description)
    if m:
        low, high = int(m.group(1)), int(m.group(2))
        my_years = experience.get("total_years", 0)
        if my_years > high + 5:
            penalty -= 10
            flags.append(f"overqualified: JD asks {low}-{high}yr experience")

    return penalty, flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_job(
    job: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Score a single job posting against *profile*.

    Parameters
    ----------
    job : dict
        Must contain at least ``title`` and ``description``.
        May contain ``company``, ``location``, ``url``, etc.
    profile : dict
        Loaded from profile.json.

    Returns
    -------
    dict with keys: score, matched_skills, gaps, flags
    """
    title = job.get("title", "")
    desc = job.get("description", "")
    company = job.get("company", "")

    title_pts = _score_title(title, profile.get("target_titles", []))
    skill_pts, matched_skills, gaps = _score_skills(
        desc, profile.get("skills", {}), profile.get("skill_variants"),
    )
    exp_pts, exp_flags = _score_experience(desc, profile.get("experience", {}))
    ind_pts = _score_industry(
        desc, company,
        profile.get("experience", {}),
        profile.get("preferences", {}),
    )
    penalty, pen_flags = _score_penalties(
        desc, title,
        profile.get("dealbreakers", []),
        profile.get("experience", {}),
    )

    flags = exp_flags + pen_flags
    raw = title_pts + skill_pts + exp_pts + ind_pts
    final = max(raw + penalty, 0)

    return {
        "score": final,
        "breakdown": {
            "title": title_pts,
            "skills": skill_pts,
            "experience": exp_pts,
            "industry": ind_pts,
            "penalty": penalty,
        },
        "matched_skills": matched_skills,
        "gaps": gaps,
        "flags": flags,
    }


def score_jobs(
    jobs: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score a batch of jobs and return results sorted best-first.

    Each element in the returned list is the original job dict with an added
    ``_score`` key containing the full scoring result.
    """
    if profile is None:
        profile = load_profile()

    results: list[dict[str, Any]] = []
    for job in jobs:
        result = {**job, "_score": score_job(job, profile)}
        results.append(result)

    results.sort(key=lambda j: j["_score"]["score"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

_DEMO_JOBS: list[dict[str, Any]] = [
    {
        "title": "Director of Product",
        "company": "Acme SaaS Corp",
        "description": (
            "We are looking for a Director of Product to own the product strategy "
            "and roadmap for our enterprise SaaS platform. You will drive P&L "
            "accountability, lead cross-functional teams, and champion data-driven "
            "decision-making. 10+ years of product management experience required. "
            "Experience with e-commerce, analytics, and agile methodologies preferred. "
            "You will manage a team of 8-12 product managers."
        ),
        "url": "https://example.com/jobs/1",
        "source": "demo",
    },
    {
        "title": "Senior Product Manager",
        "company": "Startup Inc",
        "description": (
            "Senior PM to build our dashboard product. 3-5 years experience. "
            "Must write production code in Python. SQL and Tableau required. "
            "Series A startup, equity-heavy compensation."
        ),
        "url": "https://example.com/jobs/2",
        "source": "demo",
    },
    {
        "title": "VP of Product",
        "company": "BigRetail Global",
        "description": (
            "Vice President of Product for our global e-commerce and retail platform. "
            "Lead product strategy across compliance, privacy, and GRC initiatives. "
            "12+ years experience, people leadership of 20+ person org. "
            "AI/ML and analytics experience strongly preferred. "
            "Fortune 500 company with SaaS and enterprise customers."
        ),
        "url": "https://example.com/jobs/3",
        "source": "demo",
    },
    {
        "title": "Junior Product Analyst",
        "company": "TinyStartup",
        "description": (
            "Entry-level product role. 1-2 years experience. "
            "Help the team with data pipeline and machine learning projects. "
            "Contract position, 3 months."
        ),
        "url": "https://example.com/jobs/4",
        "source": "demo",
    },
]


def _demo() -> None:
    profile = load_profile()
    scored = score_jobs(_DEMO_JOBS, profile)

    print("=" * 70)
    print("SCORER DEMO — scoring 4 sample jobs against profile.json")
    print("=" * 70)

    for job in scored:
        s = job["_score"]
        print(f"\n{'—' * 70}")
        print(f"  Title:   {job['title']}")
        print(f"  Company: {job['company']}")
        print(f"  Score:   {s['score']}  {s['breakdown']}")
        print(f"  Matched: {', '.join(s['matched_skills']) or '(none)'}")
        print(f"  Gaps:    {', '.join(s['gaps'][:5])}{'…' if len(s['gaps']) > 5 else ''}")
        if s["flags"]:
            print(f"  Flags:   {'; '.join(s['flags'])}")
    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    _demo()
