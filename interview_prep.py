"""Generate an interview prep document for a specific job.

Takes a job_id, fetches the job details and LLM insights from the DB,
and uses Claude Haiku to generate a structured prep document:
  - Company overview (from the JD)
  - Role summary and what they're looking for
  - Likely interview topics (mapped from matched skills + gaps)
  - Suggested STAR stories from your resume
  - Questions to ask the interviewer

Usage:
    python interview_prep.py <job_id>
    python interview_prep.py <job_id> --output prep.md
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

import db
from scorer import load_profile
from scorer_llm import _call_claude, _get_api_key

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "prep"


def _get_job(job_id: str) -> dict[str, Any] | None:
    conn = db.init_db()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    llm = db.get_llm_cache(conn, job_id)
    conn.close()
    if not row:
        return None
    job = dict(row)
    job["llm"] = llm
    return job


def _build_prompt(job: dict[str, Any], profile: dict[str, Any]) -> str:
    desc = (job.get("description") or "")[:4000]
    resume = profile.get("resume_context", "")
    llm = job.get("llm", {}) or {}

    matched_raw = job.get("matched_skills", "[]")
    try:
        matched = json.loads(matched_raw) if isinstance(matched_raw, str) else (matched_raw or [])
    except json.JSONDecodeError:
        matched = []

    strengths = llm.get("strengths", [])
    concerns = llm.get("concerns", [])

    return (
        f"Generate a structured interview prep document for this role.\n\n"
        f"--- CANDIDATE RESUME ---\n{resume}\n\n"
        f"--- JOB POSTING ---\n"
        f"Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Location: {job.get('location', '')}\n"
        f"Matched skills: {', '.join(matched)}\n"
        f"LLM strengths: {', '.join(strengths)}\n"
        f"LLM concerns: {', '.join(concerns)}\n"
        f"Description:\n{desc}\n\n"
        f"--- INSTRUCTIONS ---\n"
        f"Create a markdown interview prep document with these sections:\n"
        f"1. **Company Overview** — What this company does, key products, recent news if inferable\n"
        f"2. **Role Summary** — What they're looking for, key responsibilities, reporting structure\n"
        f"3. **Why You're a Strong Fit** — Map 4-5 of the candidate's specific accomplishments to their requirements. Use actual metrics from the resume.\n"
        f"4. **Potential Concerns & How to Address Them** — For each LLM concern, suggest a specific response with evidence from the resume\n"
        f"5. **Likely Interview Topics** — 6-8 topics they'll probably ask about, based on the JD\n"
        f"6. **STAR Stories to Prepare** — 4-5 specific stories from the resume, formatted as Situation/Task/Action/Result outlines\n"
        f"7. **Questions to Ask** — 5 thoughtful questions that demonstrate domain knowledge\n\n"
        f"Be specific. Reference actual accomplishments, metrics, and company names from the resume. "
        f"Do not fabricate any claims."
    )


def generate_prep(job_id: str, output_path: str | None = None) -> str | None:
    """Generate interview prep for a job. Returns the markdown content."""
    job = _get_job(job_id)
    if not job:
        print(f"Job ID '{job_id}' not found", file=sys.stderr)
        return None

    profile = load_profile()
    api_key = _get_api_key()

    print(
        f"Generating interview prep for: {job['title']} @ {job['company']}",
        file=sys.stderr,
    )

    prompt = _build_prompt(job, profile)

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    from urllib.request import Request, urlopen
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "Anthropic-Version": "2023-06-01",
    }
    req = Request("https://api.anthropic.com/v1/messages", data=body, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block.get("text", "")
    except Exception as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return None

    # Prepend header
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    content = f"# Interview Prep: {title} @ {company}\n\n{text}"

    # Save to file
    if output_path:
        out = Path(output_path)
    else:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = f"{company}-{title}".replace("/", "-").replace(" ", "-")[:80]
        out = OUTPUT_DIR / f"{safe_name}.md"

    out.write_text(content)
    print(f"Prep document written to {out}", file=sys.stderr)
    return content


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    job_id = sys.argv[1]
    output = sys.argv[3] if len(sys.argv) >= 4 and sys.argv[2] == "--output" else None
    content = generate_prep(job_id, output)
    if content:
        print(content)


if __name__ == "__main__":
    main()
