"""LLM-powered job scoring using Claude (Haiku model for cost efficiency).

Takes jobs that scored ≥50 in Tier 1 (keyword scoring) and sends them to
the Claude API for a deeper evaluation against the candidate profile.

Returns an enriched score dict with:
  llm_score (1-10), strengths, concerns, recommendation (Apply/Maybe/Skip)

Environment variables:
    ANTHROPIC_API_KEY  — Claude API key (https://console.anthropic.com)

Usage:
    python scorer_llm.py          # runs a demo with sample jobs
    python -m scorer_llm          # same
"""

import json
import os
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = "claude-haiku-4-5-20251001"
API_URL = "https://api.anthropic.com/v1/messages"
MAX_DESC_CHARS = 3000  # truncate long descriptions to manage token cost
MAX_TOKENS = 500       # response token limit (expanded for metadata fields)
REQUEST_DELAY = 1.0    # seconds between API calls


def _get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is required. "
            "Get yours at https://console.anthropic.com"
        )
    return key


def _build_prompt(job: dict[str, Any], profile: dict[str, Any]) -> str:
    desc = (job.get("description", "") or "")[:MAX_DESC_CHARS]
    tier1 = job.get("_score", {})

    # Use the rich resume context if available, otherwise fall back to structured fields
    resume_ctx = profile.get("resume_context", "")
    if resume_ctx:
        profile_summary = (
            f"{resume_ctx}\n\n"
            f"Target titles: {', '.join(profile.get('target_titles', []))}\n"
            f"Preferences: {', '.join(profile.get('preferences', {}).get('work_arrangement', []))} | "
            f"{', '.join(profile.get('preferences', {}).get('locations', []))}\n"
            f"Dealbreakers: {', '.join(profile.get('dealbreakers', []))}"
        )
    else:
        profile_summary = (
            f"Candidate: {profile.get('headline', '')}\n"
            f"Target titles: {', '.join(profile.get('target_titles', []))}\n"
            f"High-weight skills: {', '.join(profile.get('skills', {}).get('high', []))}\n"
            f"Medium-weight skills: {', '.join(profile.get('skills', {}).get('medium', []))}\n"
            f"Experience: {profile.get('experience', {}).get('total_years', 0)} years total, "
            f"{profile.get('experience', {}).get('management_years', 0)} years management, "
            f"largest team {profile.get('experience', {}).get('largest_team_size', 0)}\n"
            f"Industries: {', '.join(profile.get('experience', {}).get('industries', []))}\n"
            f"Notable companies: {', '.join(profile.get('experience', {}).get('notable_companies', []))}\n"
            f"Preferences: {', '.join(profile.get('preferences', {}).get('work_arrangement', []))} | "
            f"{', '.join(profile.get('preferences', {}).get('locations', []))}\n"
            f"Dealbreakers: {', '.join(profile.get('dealbreakers', []))}"
        )

    return (
        f"You are evaluating a job posting for fit with this candidate. "
        f"Score 1-10, list top 3 strengths, top 3 gaps/concerns, and give a "
        f"recommendation: Apply/Maybe/Skip. Be specific and reference actual "
        f"skills and experience.\n\n"
        f"--- HARD CONSTRAINTS (override all positive signals) ---\n"
        f"This candidate is a technical Product Manager, not a marketer. "
        f"Return recommendation \"Skip\" and llm_score ≤ 3 for any role whose "
        f"title is in the marketing family — including Product Marketing "
        f"(PMM), Growth Marketing, Demand Generation, Brand Marketing, Field "
        f"Marketing, Content Marketing, Marketing Manager/Director, Head of "
        f"Marketing, VP of Marketing, or CMO — even if skill keywords "
        f"overlap (e.g. go-to-market, product strategy, cross-functional). "
        f"Titles like \"Product Manager, Marketing Technology\" are product "
        f"roles and are fine.\n\n"
        f"--- CANDIDATE PROFILE ---\n{profile_summary}\n\n"
        f"Companies where the candidate has personal referrals "
        f"(strong network advantage — bias toward Apply if other factors align): "
        f"{', '.join(profile.get('referrals', []))}\n\n"
        f"--- JOB POSTING ---\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Source: {job.get('source', '')}\n"
        f"Tier 1 score: {tier1.get('score', 'N/A')}\n"
        f"Has referral at this company: {tier1.get('has_referral', False)}\n"
        f"Tier 1 matched skills: {', '.join(tier1.get('matched_skills', []))}\n"
        f"Description:\n{desc}\n\n"
        f"--- INSTRUCTIONS ---\n"
        f"Respond in this exact JSON format (no markdown, no code fences):\n"
        f'{{"llm_score": <1-10>, "strengths": ["...", "...", "..."], '
        f'"concerns": ["...", "...", "..."], "recommendation": "Apply|Maybe|Skip", '
        f'"salary_range": "$150K-$200K or null if not mentioned", '
        f'"team_size": "10-15 or null if not mentioned", '
        f'"reports_to": "VP Engineering or null if not mentioned", '
        f'"role_type": "manager|executive|IC"}}'
    )


def _parse_llm_json(text: str) -> dict[str, Any]:
    """Parse JSON from Claude's response, handling common formatting issues.

    Uses multiple fallback strategies to extract whatever structured data
    is available. Returns a partial dict rather than raising — callers can
    check for empty/missing fields.
    """
    import re as _re

    text = (text or "").strip()

    # Empty response — return None so caller can skip
    if not text:
        return None  # type: ignore

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        text = _re.sub(r"^```(?:json)?\s*\n?", "", text)
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    if not text:
        return None  # type: ignore

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract JSON object from surrounding text
    start = text.find("{")
    end = text.rfind("}")
    json_str = ""
    if start >= 0 and end > start:
        json_str = text[start:end + 1]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # Attempt repairs on the extracted JSON
        repaired = json_str
        repaired = _re.sub(r'(?<=": ")(.*?)(?="[,}])', lambda m: m.group(0).replace("\n", "\\n"), repaired, flags=_re.DOTALL)
        repaired = _re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Last resort: regex-extract whatever fields we can find from the text
    # (works even if the text has no {} at all — just raw "key": "value" pairs)
    search_text = json_str if json_str else text
    score_m = _re.search(r'"?llm_score"?\s*:\s*(\d+)', search_text)
    rec_m = _re.search(r'"?recommendation"?\s*:\s*"?(Apply|Maybe|Skip)"?', search_text, _re.I)

    if score_m or rec_m:
        return {
            "llm_score": int(score_m.group(1)) if score_m else 5,
            "recommendation": rec_m.group(1).title() if rec_m else "Maybe",
            "strengths": _extract_list(search_text, "strengths"),
            "concerns": _extract_list(search_text, "concerns"),
            "salary_range": _extract_field(search_text, "salary_range"),
            "team_size": _extract_field(search_text, "team_size"),
            "reports_to": _extract_field(search_text, "reports_to"),
            "role_type": _extract_field(search_text, "role_type"),
            "parse_note": "partial (regex fallback)",
        }

    # Truly unparseable — return None instead of raising
    return None  # type: ignore


def _extract_list(text: str, key: str) -> list[str]:
    """Extract a JSON array field by regex from malformed JSON."""
    import re as _re
    m = _re.search(rf'"{key}"\s*:\s*\[(.*?)\]', text, _re.DOTALL)
    if m:
        items = _re.findall(r'"([^"]*)"', m.group(1))
        return items[:3]
    return []


def _extract_field(text: str, key: str) -> str | None:
    """Extract a single JSON string field by regex."""
    import re as _re
    m = _re.search(rf'"{key}"\s*:\s*"([^"]*)"', text)
    if m:
        val = m.group(1)
        return val if val.lower() != "null" else None
    return None


def _call_claude(prompt: str, api_key: str, retries: int = 2) -> dict[str, Any] | None:
    """Call the Claude API and parse the JSON response."""
    body = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "Anthropic-Version": "2023-06-01",
    }

    req = Request(API_URL, data=body, headers=headers, method="POST")

    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            # Extract text from the response
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block.get("text", "")
            # Parse the JSON from Claude's response
            parsed = _parse_llm_json(text)
            if parsed is None:
                # Empty or unparseable response — log first 100 chars for debugging
                stop_reason = data.get("stop_reason", "?")
                print(f"    Empty/unparseable response (stop={stop_reason}): {repr(text[:100])}", file=sys.stderr)
                return None
            return parsed
        except HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    Rate limited — waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if exc.code == 529:  # overloaded
                time.sleep(3)
                continue
            print(f"    Claude API error {exc.code}: {exc.read().decode()[:200]}", file=sys.stderr)
            return None
        except (URLError, OSError) as exc:
            if attempt < retries:
                time.sleep(2)
                continue
            print(f"    Network error: {exc}", file=sys.stderr)
            return None
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"    Failed to parse Claude response: {exc}", file=sys.stderr)
            return None

    return None


def _promote_high_confidence(result: dict[str, Any] | None) -> dict[str, Any] | None:
    # A Maybe at llm_score >= 9 reads as model over-hedging — promote to Apply.
    # Skip is left alone: a high score with Skip signals a real conflict (e.g.
    # dealbreaker) that the model wants to flag.
    if not result:
        return result
    try:
        score = int(result.get("llm_score", 0))
    except (TypeError, ValueError):
        return result
    if score >= 9 and result.get("recommendation") == "Maybe":
        result = dict(result)
        result["recommendation"] = "Apply"
    return result


def score_job_llm(
    job: dict[str, Any],
    profile: dict[str, Any],
    api_key: str | None = None,
    db_conn: Any = None,
) -> dict[str, Any] | None:
    """Score a single job with Claude. Returns the LLM result dict or None.

    If *db_conn* is provided, checks the LLM cache first and skips the
    API call if a cached response exists.
    """
    if api_key is None:
        api_key = _get_api_key()

    # Check cache
    if db_conn is not None:
        from db import job_id as make_jid, get_llm_cache, set_llm_cache
        jid = make_jid(
            job.get("title", ""), job.get("company", ""), job.get("url", "")
        )
        cached = get_llm_cache(db_conn, jid)
        if cached:
            return _promote_high_confidence(cached)

    prompt = _build_prompt(job, profile)
    result = _call_claude(prompt, api_key)

    # Store in cache
    if result and db_conn is not None:
        set_llm_cache(db_conn, jid, result)

    return _promote_high_confidence(result)


def score_jobs_llm(
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    threshold: int = 50,
    db_conn: Any = None,
) -> list[dict[str, Any]]:
    """LLM-score jobs above *threshold* and merge results into _score.

    Jobs below threshold are returned unchanged. Jobs that fail the API
    call are also returned unchanged. Cached responses are reused.
    """
    api_key = _get_api_key()

    eligible = [j for j in jobs if j.get("_score", {}).get("score", 0) >= threshold]
    print(
        f"LLM scoring: {len(eligible)} of {len(jobs)} jobs above threshold {threshold}",
        file=sys.stderr,
    )

    cached_count = 0
    for i, job in enumerate(eligible):
        title = job.get("title", "?")
        company = job.get("company", "?")
        print(f"  [{i+1}/{len(eligible)}] {title} @ {company} …", file=sys.stderr, end=" ")

        result = score_job_llm(job, profile, api_key, db_conn=db_conn)
        if result:
            job.setdefault("_score", {})["llm"] = result
            rec = result.get("recommendation", "?")
            llm_score = result.get("llm_score", "?")
            # Check if it came from cache (no delay needed)
            from db import job_id as make_jid, get_llm_cache
            if db_conn and get_llm_cache(db_conn, make_jid(
                job.get("title", ""), job.get("company", ""), job.get("url", "")
            )):
                cached_count += 1
                print(f"score={llm_score}, rec={rec} (cached)", file=sys.stderr)
                continue
            print(f"score={llm_score}, rec={rec}", file=sys.stderr)
        else:
            print("failed", file=sys.stderr)

        time.sleep(REQUEST_DELAY)

    if cached_count:
        print(f"  {cached_count} results from cache (saved API calls)", file=sys.stderr)

    # Re-sort: boost jobs with Apply recommendation
    def _sort_key(j: dict[str, Any]) -> tuple[int, int]:
        s = j.get("_score", {})
        llm = s.get("llm", {})
        rec_boost = {"Apply": 2, "Maybe": 1, "Skip": 0}.get(
            llm.get("recommendation", ""), 0
        )
        return (rec_boost, s.get("score", 0))

    jobs.sort(key=_sort_key, reverse=True)
    return jobs


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

_DEMO_JOB = {
    "title": "Director of Product",
    "company": "Acme SaaS Corp",
    "location": "Seattle, WA (Remote eligible)",
    "description": (
        "We are looking for a Director of Product to own the product strategy "
        "and roadmap for our enterprise SaaS platform. You will drive P&L "
        "accountability, lead cross-functional teams, and champion data-driven "
        "decision-making. 10+ years of product management experience required. "
        "Experience with e-commerce, analytics, compliance, and agile "
        "methodologies preferred. You will manage a team of 8-12 product "
        "managers. AI/ML experience is a plus."
    ),
    "url": "https://example.com/jobs/1",
    "source": "demo",
    "_score": {
        "score": 86,
        "matched_skills": [
            "product strategy", "roadmap", "P&L", "data-driven",
            "cross-functional", "enterprise", "SaaS", "e-commerce",
            "analytics", "agile", "compliance",
        ],
        "gaps": ["GRC", "AI/ML", "instrumentation"],
        "flags": [],
    },
}


def _demo() -> None:
    from scorer import load_profile

    profile = load_profile()

    print("=" * 60, file=sys.stderr)
    print("LLM SCORER DEMO", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    result = score_job_llm(_DEMO_JOB, profile)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print("LLM scoring failed (check ANTHROPIC_API_KEY)", file=sys.stderr)


if __name__ == "__main__":
    _demo()
