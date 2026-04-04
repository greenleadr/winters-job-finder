"""Evaluate a manually submitted job description.

Called by the track-issue.yml workflow when an issue with [EVALUATE]
prefix is created, or standalone via CLI.

Usage:
    python evaluate.py --title "Dir Product" --company "Stripe" --file jd.txt
    ISSUE_TITLE="[EVALUATE]" ISSUE_BODY="..." python evaluate.py
"""

import json
import os
import sys
from typing import Any

from scorer import score_job, load_profile, _load_target_companies, _extract_salary
from scorer_llm import score_job_llm


def evaluate(
    title: str,
    company: str,
    location: str,
    description: str,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Score a job description against the profile. Returns full results."""
    profile = load_profile()
    profile["_target_companies"] = _load_target_companies()

    job = {
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "url": "",
        "source": "manual",
    }

    # Tier 1
    tier1 = score_job(job, profile)

    result = {
        "title": title,
        "company": company,
        "location": location,
        "tier1": tier1,
    }

    # Tier 2 (LLM)
    if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
        job["_score"] = tier1
        try:
            llm = score_job_llm(job, profile)
            result["tier2"] = llm
        except Exception as exc:
            result["tier2_error"] = str(exc)

    return result


def format_result(result: dict[str, Any]) -> str:
    """Format evaluation result as readable text."""
    t1 = result["tier1"]
    bd = t1["breakdown"]
    lines = []

    lines.append(f"## Evaluation: {result['title']} @ {result['company']}")
    lines.append("")
    lines.append(f"**Tier 1 Score: {t1['score']} / 100**")
    lines.append("")
    lines.append("| Component | Points |")
    lines.append("|-----------|--------|")
    lines.append(f"| Title | {bd['title']} / 30 |")
    lines.append(f"| Skills | {bd['skills']} / 40 |")
    lines.append(f"| Experience | {bd['experience']} / 15 |")
    lines.append(f"| Industry | {bd['industry']} / 15 |")
    lines.append(f"| Company boost | +{bd.get('company_boost', 0)} |")
    lines.append(f"| Desc boost | +{bd.get('desc_boost', 0)} |")
    lines.append(f"| Penalty | {bd['penalty']} |")
    lines.append("")

    if t1.get("salary_min") and t1.get("salary_max"):
        lines.append(f"**Salary**: ${t1['salary_min']//1000}K–${t1['salary_max']//1000}K")
        lines.append("")

    lines.append(f"**Matched Skills** ({len(t1['matched_skills'])}): {', '.join(t1['matched_skills'])}")
    lines.append("")
    lines.append(f"**Not in JD** ({len(t1['gaps'])}): {', '.join(t1['gaps'][:10])}")

    if t1.get("flags"):
        lines.append("")
        lines.append(f"**Flags**: {'; '.join(t1['flags'])}")

    t2 = result.get("tier2")
    if t2:
        lines.append("")
        lines.append("---")
        lines.append("### Tier 2 (Claude Haiku)")
        lines.append("")
        lines.append(f"**LLM Score**: {t2.get('llm_score', '?')} / 10")
        lines.append(f"**Recommendation**: {t2.get('recommendation', '?')}")
        if t2.get("role_type"):
            lines.append(f"**Role Type**: {t2['role_type']}")
        if t2.get("team_size"):
            lines.append(f"**Team Size**: {t2['team_size']}")
        if t2.get("reports_to"):
            lines.append(f"**Reports To**: {t2['reports_to']}")
        if t2.get("salary_range"):
            lines.append(f"**LLM Salary**: {t2['salary_range']}")
        lines.append("")
        if t2.get("strengths"):
            lines.append("**Strengths**:")
            for s in t2["strengths"]:
                lines.append(f"- {s}")
        if t2.get("concerns"):
            lines.append("")
            lines.append("**Concerns**:")
            for c in t2["concerns"]:
                lines.append(f"- {c}")

    if result.get("tier2_error"):
        lines.append("")
        lines.append(f"*LLM scoring failed: {result['tier2_error']}*")

    return "\n".join(lines)


def process_evaluate_issue() -> str | None:
    """Parse an [EVALUATE] GitHub Issue and return formatted results."""
    body = os.environ.get("ISSUE_BODY", "")
    if not body:
        return None

    # Parse structured fields from issue body
    title = ""
    company = ""
    location = ""
    description = ""

    current_section = None
    for line in body.split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("Title:"):
            title = line_stripped[6:].strip()
        elif line_stripped.startswith("Company:"):
            company = line_stripped[8:].strip()
        elif line_stripped.startswith("Location:"):
            location = line_stripped[9:].strip()
        elif line_stripped.startswith("Description:"):
            current_section = "description"
            desc_inline = line_stripped[12:].strip()
            if desc_inline:
                description = desc_inline + "\n"
        elif current_section == "description":
            description += line + "\n"

    if not description.strip():
        description = body  # fallback: use entire body as description

    result = evaluate(
        title=title or "Unknown",
        company=company or "Unknown",
        location=location or "",
        description=description.strip(),
    )
    return format_result(result)


def main():
    # Check if called from GitHub Actions
    if os.environ.get("ISSUE_TITLE", "").startswith("[EVALUATE]"):
        output = process_evaluate_issue()
        if output:
            print(output)
        return

    # CLI mode
    import argparse
    parser = argparse.ArgumentParser(description="Evaluate a job description")
    parser.add_argument("--title", default="", help="Job title")
    parser.add_argument("--company", default="", help="Company name")
    parser.add_argument("--location", default="", help="Location")
    parser.add_argument("--file", default="", help="Read JD from file")
    parser.add_argument("--no-llm", action="store_true", help="Skip Tier 2 LLM scoring")
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            description = f.read()
    else:
        print("Paste job description (Ctrl+D or Ctrl+Z when done):", file=sys.stderr)
        description = sys.stdin.read()

    title = args.title or input("Title: ") if not args.title else args.title
    company = args.company or input("Company: ") if not args.company else args.company

    result = evaluate(
        title=title,
        company=company,
        location=args.location,
        description=description,
        use_llm=not args.no_llm,
    )
    print(format_result(result))


if __name__ == "__main__":
    main()
