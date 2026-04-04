"""Adzuna job search collector.

Queries the Adzuna API for product leadership roles matching the
target profile. Requires ADZUNA_APP_ID and ADZUNA_API_KEY environment
variables (register at https://developer.adzuna.com).

Usage:
    python -m collectors.adzuna
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://api.adzuna.com/v1/api/jobs/us/search"

SEARCH_TITLES = [
    "VP Product",
    "Director Product",
    "Head of Product",
    "Vice President Product",
    "Senior Director Product",
    "Senior Manager Product",
    "Associate Director Product",
    "Group Product Manager",
    "Principal Product Manager",
    "Director Product Management",
    "Director Technical Product",
    "Senior Product Manager",
]

LOCATIONS = ["Seattle", "Pittsburgh", "Whidbey Island", "Mount Vernon", "Everett", "Remote"]
RESULTS_PER_PAGE = 50
MAX_PAGES = 5
REQUEST_DELAY_SECONDS = 1.0


def _get_credentials() -> tuple[str, str]:
    app_id = os.environ.get("ADZUNA_APP_ID", "")
    api_key = os.environ.get("ADZUNA_API_KEY", "")
    if not app_id or not api_key:
        raise RuntimeError(
            "ADZUNA_APP_ID and ADZUNA_API_KEY environment variables are required. "
            "Register at https://developer.adzuna.com to obtain them."
        )
    return app_id, api_key


def _make_request(url: str, retries: int = 3) -> dict[str, Any]:
    """Fetch JSON from *url* with retry/back-off for rate limits."""
    req = Request(url, headers={"Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"  Rate limited – retrying in {wait}s …", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except URLError as exc:
            if attempt < retries - 1:
                wait = 2 ** (attempt + 1)
                print(
                    f"  Network error ({exc.reason}) – retrying in {wait}s …",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"Request failed after {retries} retries: {url}")


def _parse_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": result.get("title", ""),
        "company": (result.get("company", {}) or {}).get("display_name", ""),
        "location": (result.get("location", {}) or {}).get("display_name", ""),
        "url": result.get("redirect_url", ""),
        "description": result.get("description", ""),
        "source": "adzuna",
        "date_posted": result.get("created", ""),
    }


def _matches_target_location(result: dict[str, Any]) -> bool:
    """Return True if the listing is in a target location."""
    location_name = (
        (result.get("location", {}) or {}).get("display_name", "")
    ).lower()

    target_cities = [
        "seattle", "bellevue", "redmond", "kirkland", "bothell",
        "renton", "kent", "tacoma", "everett",
        "whidbey", "oak harbor",
        "pittsburgh",
        "victoria",
    ]
    if any(s in location_name for s in target_cities):
        return True

    # Adzuna US API returns mostly US jobs, so "remote" here is likely US
    # (main.py's stricter filter will catch any non-US remote that slips through)
    if "remote" in location_name or "work from home" in location_name:
        return True

    return False


def search_jobs() -> list[dict[str, Any]]:
    """Query Adzuna for product leadership roles and return normalized results."""
    app_id, api_key = _get_credentials()
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for title_query in SEARCH_TITLES:
      for location in LOCATIONS:
        print(f"Searching: {title_query!r} in {location}", file=sys.stderr)

        for page in range(1, MAX_PAGES + 1):
            params = urlencode(
                {
                    "app_id": app_id,
                    "app_key": api_key,
                    "what": title_query,
                    "where": location,
                    "results_per_page": RESULTS_PER_PAGE,
                    "content-type": "application/json",
                }
            )
            url = f"{BASE_URL}/{page}?{params}"

            try:
                data = _make_request(url)
            except (HTTPError, URLError, RuntimeError) as exc:
                print(f"  Error on page {page}: {exc}", file=sys.stderr)
                break

            results = data.get("results", [])
            if not results:
                break

            for result in results:
                if not _matches_target_location(result):
                    continue
                parsed = _parse_result(result)
                if parsed["url"] and parsed["url"] not in seen_urls:
                    seen_urls.add(parsed["url"])
                    all_jobs.append(parsed)

            total_count = data.get("count", 0)
            fetched = page * RESULTS_PER_PAGE
            if fetched >= total_count:
                break

            time.sleep(REQUEST_DELAY_SECONDS)

        time.sleep(REQUEST_DELAY_SECONDS)

    print(f"Found {len(all_jobs)} jobs total.", file=sys.stderr)
    return all_jobs


if __name__ == "__main__":
    jobs = search_jobs()
    print(json.dumps(jobs, indent=2))
