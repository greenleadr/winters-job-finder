"""Remotive.com collector (stub).

Placeholder for querying the Remotive API for remote product-leadership
roles.  Returns an empty list until implemented.

Usage:
    python -m collectors.remotive
"""

import json
import sys
from typing import Any


def search_jobs() -> list[dict[str, Any]]:
    """Query Remotive for remote product leadership roles."""
    # TODO: implement using https://remotive.com/api/remote-jobs?category=product
    print("remotive collector: not yet implemented — returning []", file=sys.stderr)
    return []


if __name__ == "__main__":
    jobs = search_jobs()
    print(json.dumps(jobs, indent=2))
