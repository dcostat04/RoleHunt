"""Hacker News "Ask HN: Who is hiring?" via the Algolia HN API.

Finds the latest monthly thread posted by `whoishiring` and turns each
top-level comment into a Job. By convention the first line of a comment is a
pipe-separated header: "Company | Role | Location | Remote | ...".
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from job_scout.models import Job, Source
from job_scout.scrapers.base import Scraper

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://hn.algolia.com/api/v1/items/{item_id}"

_STRONG_LOCATION = re.compile(
    r"\b(remote|onsite|on-site|in-office|hybrid|anywhere|worldwide|global|timezones?)\b", re.IGNORECASE
)
_CITY_COUNTRY = re.compile(r"^[A-Z][\w.'\- ]+,\s*[A-Z][\w.'\- ]+$")  # "City, Country" / "City, ST"
_ROLE_HINT = re.compile(
    r"\b(manager|engineer|designer|lead|head|director|developer|analyst|scientist|product|"
    r"founder|officer|architect|researcher|marketer|recruiter|pm|apm|sre|devops)s?\b",
    re.IGNORECASE,
)
# Header parts that are neither the role nor the location.
_META_HINT = re.compile(
    r"full[- ]?time|part[- ]?time|contract|intern(ship)?|visa|salary|equity|"
    r"[$€£₹]|\b\d+\s*k\b|\bhttps?://|\b(series [a-e]|seed|yc [wsf]\d+)\b",
    re.IGNORECASE,
)


class HackerNewsScraper(Scraper):
    source = Source.HACKERNEWS

    async def scrape(self) -> list[Job]:
        resp = await self.get(SEARCH_URL, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
        thread_id = find_hiring_thread(resp.json())
        if thread_id is None:
            return []
        resp = await self.get(ITEM_URL.format(item_id=thread_id))
        return parse_thread(resp.json())


def find_hiring_thread(search_payload: dict) -> str | None:
    hits = search_payload.get("hits", [])
    hiring = [h for h in hits if (h.get("title") or "").lower().startswith("ask hn: who is hiring")]
    if not hiring:
        return None
    hiring.sort(key=lambda h: h.get("created_at_i", 0), reverse=True)
    return str(hiring[0]["objectID"])


def parse_thread(item: dict) -> list[Job]:
    jobs: list[Job] = []
    for child in item.get("children") or []:
        job = parse_comment(child)
        if job:
            jobs.append(job)
    return jobs


def parse_comment(comment: dict) -> Job | None:
    html = comment.get("text")
    if not html or comment.get("author") is None:  # deleted / dead
        return None

    # HN separates paragraphs with <p>; make that a newline so line 1 is the header.
    soup = BeautifulSoup(html.replace("<p>", "\n<p>"), "lxml")
    text = soup.get_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    header = lines[0]
    parts = [p.strip() for p in header.split("|") if p.strip()]
    if len(parts) > 1:
        company = parts[0]
        location_parts = [p for p in parts[1:] if _is_location(p)]
        roles = [p for p in parts[1:] if p not in location_parts and not _META_HINT.search(p)]
        title = roles[0] if roles else header
        location = " | ".join(location_parts)
    else:  # free-form header: keep it whole and let the filters read the body
        company, title, location = "", header, ""

    posted_at = None
    if comment.get("created_at"):
        try:
            posted_at = datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
        except ValueError:
            pass

    comment_id = str(comment.get("id", ""))
    return Job(
        source=Source.HACKERNEWS,
        title=title[:300],
        company=company[:120],
        location=location[:200],
        description="\n".join(lines),  # keep the header: it may list more roles
        url=f"https://news.ycombinator.com/item?id={comment_id}",
        posted_at=posted_at,
        external_id=comment_id,
    )


def _is_location(part: str) -> bool:
    if _ROLE_HINT.search(part):
        # "Remote" can still sit alongside a role ("PM (Remote)"), but then the
        # part is primarily a role; treat it as one.
        return False
    return bool(_STRONG_LOCATION.search(part) or _CITY_COUNTRY.match(part))
