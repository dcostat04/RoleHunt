"""Wellfound (formerly AngelList Talent) role/location listing pages.

Wellfound is a Next.js app; listing data is embedded as an Apollo cache in
<script id="__NEXT_DATA__">. The parser walks that cache generically instead
of hard-coding a path, because the exact shape shifts between deploys.

Wellfound uses aggressive bot protection. From datacenter IPs (including
GitHub Actions runners) requests are often answered with 403; that is
reported as a source error and the rest of the run continues.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from job_scout.models import Job, Source
from job_scout.scrapers.base import Scraper

LISTING_URL = "https://wellfound.com/role/l/{role}/{location}"


class WellfoundScraper(Scraper):
    source = Source.WELLFOUND

    async def scrape(self) -> list[Job]:
        s = self.settings.search
        urls = [LISTING_URL.format(role=s.wellfound_role, location=loc) for loc in s.wellfound_locations]
        results = await asyncio.gather(*(self._fetch(u) for u in urls), return_exceptions=True)

        jobs: list[Job] = []
        errors: list[BaseException] = []
        for result in results:
            if isinstance(result, BaseException):
                errors.append(result)
            else:
                jobs.extend(result)
        if errors and not jobs:
            raise errors[0]
        return jobs

    async def _fetch(self, url: str) -> list[Job]:
        resp = await self.get(url, headers={"Accept": "text/html,application/xhtml+xml"}, attempts=2)
        return parse_listing_page(resp.text)


def parse_listing_page(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return []
    data = json.loads(script.string)
    apollo = _find_apollo_cache(data)
    if not apollo:
        return []
    return _jobs_from_apollo(apollo)


def _find_apollo_cache(data: Any) -> dict | None:
    """Locate the flat Apollo normalized cache (a dict of 'Typename:id' -> object)."""
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            keys = list(node.keys())
            if keys and sum(":" in k for k in keys[:50]) > len(keys[:50]) // 2 and any(
                isinstance(v, dict) and "__typename" in v for v in list(node.values())[:50]
            ):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


def _deref(apollo: dict, value: Any) -> Any:
    if isinstance(value, dict) and "__ref" in value:
        return apollo.get(value["__ref"], {})
    if isinstance(value, dict) and value.get("type") == "id" and "id" in value:
        return apollo.get(value["id"], {})
    return value


def _jobs_from_apollo(apollo: dict) -> list[Job]:
    # Map each job listing key to the startup that references it.
    owner: dict[str, dict] = {}
    for obj in apollo.values():
        if not isinstance(obj, dict) or not str(obj.get("__typename", "")).startswith("Startup"):
            continue
        for value in obj.values():
            refs = value if isinstance(value, list) else [value]
            for ref in refs:
                if isinstance(ref, dict):
                    key = ref.get("__ref") or ref.get("id")
                    if isinstance(key, str) and key.startswith("JobListing"):
                        owner[key] = obj

    jobs: list[Job] = []
    for key, obj in apollo.items():
        if not isinstance(obj, dict) or not str(obj.get("__typename", "")).startswith("JobListing"):
            continue
        title = obj.get("title")
        job_id = obj.get("id")
        if not title or not job_id:
            continue

        startup = owner.get(key) or _deref(apollo, obj.get("startup")) or {}
        slug = obj.get("slug") or ""
        locations = obj.get("locationNames") or []
        if isinstance(locations, dict):  # older apollo shape: {"type": "json", "json": [...]}
            locations = locations.get("json", [])
        location = ", ".join(locations)
        if obj.get("remote"):
            location = f"Remote{', ' + location if location else ''}"

        posted_at = None
        ts = obj.get("liveStartAt")
        if isinstance(ts, (int, float)):
            posted_at = datetime.fromtimestamp(ts, tz=timezone.utc)

        comp = obj.get("compensation") or ""
        description = "\n".join(
            part for part in (startup.get("highConcept", ""), f"Compensation: {comp}" if comp else "",
                              _strip_html(obj.get("description", ""))) if part
        )
        jobs.append(
            Job(
                source=Source.WELLFOUND,
                title=title,
                company=startup.get("name", ""),
                location=location,
                description=description,
                url=f"https://wellfound.com/jobs/{job_id}" + (f"-{slug}" if slug else ""),
                posted_at=posted_at,
                external_id=str(job_id),
            )
        )
    return jobs


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text("\n", strip=True)
