"""Career-pages collector (stub).

Placeholder for scraping individual company career pages listed in
companies.json.  Returns an empty list until implemented.

Usage:
    python -m collectors.career_pages
"""

import json
import sys
from pathlib import Path
from typing import Any


def search_jobs() -> list[dict[str, Any]]:
    """Query company career pages for product leadership roles."""
    # TODO: implement per-ATS scrapers (greenhouse, lever, workday)
    print("career_pages collector: not yet implemented — returning []", file=sys.stderr)
    return []


if __name__ == "__main__":
    jobs = search_jobs()
    print(json.dumps(jobs, indent=2))
