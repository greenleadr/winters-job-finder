"""Government jobs collector for Washington State counties.

Queries NEOGOV/GovernmentJobs.com RSS feeds for Island, Skagit, and
Snohomish counties. No API key required — public RSS feeds.

Usage:
    python -m collectors.gov_jobs
"""

import json
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

# NEOGOV RSS feed URLs for target counties
# Format: https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=<agency>
FEEDS = [
    {
        "name": "Island County",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=islandcounty",
        "careers_url": "https://www.governmentjobs.com/careers/islandcounty",
    },
    {
        "name": "Skagit County",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=skagitwa",
        "careers_url": "https://www.governmentjobs.com/careers/skagitwa",
    },
    {
        "name": "Snohomish County",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=snohomish",
        "careers_url": "https://www.governmentjobs.com/careers/snohomish",
    },
]

# Also include cities within these counties
CITY_FEEDS = [
    {
        "name": "City of Oak Harbor",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=oakharbor",
        "careers_url": "https://www.governmentjobs.com/careers/oakharbor",
    },
    {
        "name": "City of Everett",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=everettwa",
        "careers_url": "https://www.governmentjobs.com/careers/everettwa",
    },
    {
        "name": "City of Mount Vernon",
        "url": "https://www.governmentjobs.com/SearchEngine/JobsFeed?agency=mtvernonwa",
        "careers_url": "https://www.governmentjobs.com/careers/mtvernonwa",
    },
]

ALL_FEEDS = FEEDS + CITY_FEEDS


def _fetch_rss(url: str) -> str | None:
    """Fetch RSS feed XML."""
    req = Request(url, headers={
        "Accept": "application/rss+xml, application/xml, text/xml",
        "User-Agent": "WintersJobFinder/1.0",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.read().decode()
    except (HTTPError, URLError, OSError) as exc:
        print(f"    Feed error: {exc}", file=sys.stderr)
        return None


def _strip_html(text: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", clean).strip()


def _parse_feed(xml_text: str, agency_name: str, careers_url: str) -> list[dict[str, Any]]:
    """Parse RSS XML and return normalized job dicts."""
    jobs: list[dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or "")
        pub_date = (item.findtext("pubDate") or "").strip()

        if not title:
            continue

        # Extract location from description if present
        location = f"{agency_name}, WA"

        jobs.append({
            "title": title,
            "company": agency_name,
            "location": location,
            "url": link or careers_url,
            "description": desc,
            "source": "gov_jobs",
            "date_posted": pub_date,
        })

    return jobs


def search_jobs() -> list[dict[str, Any]]:
    """Query all government job RSS feeds."""
    all_jobs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for feed in ALL_FEEDS:
        name = feed["name"]
        print(f"  {name} …", file=sys.stderr, end=" ")

        xml = _fetch_rss(feed["url"])
        if not xml:
            print("no data", file=sys.stderr)
            continue

        jobs = _parse_feed(xml, name, feed["careers_url"])

        for job in jobs:
            if job["url"] and job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                all_jobs.append(job)

        print(f"{len(jobs)} jobs", file=sys.stderr)
        time.sleep(0.5)

    print(f"  gov_jobs: {len(all_jobs)} total government jobs", file=sys.stderr)
    return all_jobs


if __name__ == "__main__":
    jobs = search_jobs()
    print(json.dumps(jobs, indent=2))
