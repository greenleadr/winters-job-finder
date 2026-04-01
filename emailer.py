"""Send the HTML job digest via Brevo SMTP.

Environment variables:
    BREVO_SMTP_KEY  — Brevo SMTP API key (used as the SMTP password)
    EMAIL_TO        — recipient address (or comma-separated list)

Usage:
    python emailer.py          # sends a demo digest to EMAIL_TO
    python -m emailer          # same
"""

import os
import smtplib
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587
SENDER = "Winters Job Finder <noreply@wintersjobfinder.com>"


def _get_config() -> tuple[str, list[str]]:
    smtp_key = os.environ.get("BREVO_SMTP_KEY", "")
    if not smtp_key:
        raise RuntimeError(
            "BREVO_SMTP_KEY environment variable is required. "
            "Get yours at https://app.brevo.com/settings/keys/smtp"
        )
    raw_to = os.environ.get("EMAIL_TO", "")
    if not raw_to:
        raise RuntimeError(
            "EMAIL_TO environment variable is required (comma-separated for multiple)."
        )
    recipients = [addr.strip() for addr in raw_to.split(",") if addr.strip()]
    return smtp_key, recipients


def send_digest(
    html_body: str,
    job_count: int,
    run_date: date | None = None,
    smtp_key: str | None = None,
    recipients: list[str] | None = None,
) -> None:
    """Send *html_body* as the digest email.

    Parameters
    ----------
    html_body : str
        Full HTML string (from ``digest.generate_digest``).
    job_count : int
        Number of jobs in the digest (used in the subject line).
    run_date : date, optional
        Defaults to today.
    smtp_key : str, optional
        Overrides BREVO_SMTP_KEY env var.
    recipients : list[str], optional
        Overrides EMAIL_TO env var.
    """
    today = run_date or date.today()
    date_str = today.strftime("%Y-%m-%d")

    if smtp_key is None or recipients is None:
        env_key, env_to = _get_config()
        smtp_key = smtp_key or env_key
        recipients = recipients or env_to

    subject = f"Job Digest — {date_str} — {job_count} matches"

    msg = MIMEMultipart("alternative")
    msg["From"] = SENDER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    # Plain-text fallback
    plain = (
        f"Winters Job Finder — {date_str}\n\n"
        f"{job_count} new job matches found.\n"
        f"View the HTML version of this email for the full digest."
    )
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    print(f"Connecting to {SMTP_HOST}:{SMTP_PORT} …", file=sys.stderr)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SENDER, smtp_key)
        server.sendmail(SENDER, recipients, msg.as_string())

    print(
        f"Digest sent to {', '.join(recipients)} "
        f"({job_count} matches, {date_str})",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    from digest import generate_digest
    from scorer import _DEMO_JOBS, load_profile, score_jobs

    profile = load_profile()
    scored = score_jobs(_DEMO_JOBS, profile)
    html_body = generate_digest(scored)

    send_digest(html_body, job_count=len(scored))


if __name__ == "__main__":
    _demo()
