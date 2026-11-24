"""LinkedIn via the public (logged-out) jobs-guest endpoints.

Search results come back as an HTML fragment of <li> cards. Descriptions are
not in the search response, so they are fetched lazily in `enrich()` only for
jobs that survive the keyword and location filters.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from bs4 import BeautifulSoup

from job_scout.models import Job, Source
from job_scout.scrapers.base import Scraper

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 25

_JOB_ID_RE = re.compile(r"(\d{6,})")


class LinkedInScraper(Scraper):
    source = Source.LINKEDIN

    async def scrape(self) -> list[Job]:
        s = self.settings.search
        tasks = [
            self._search(query, location)
            for query in s.queries
            for location in s.linkedin_locations
        ]
        pages = await asyncio.gather(*tasks)
        return [job for page in pages for job in page]

    async def _search(self, query: str, location: str) -> list[Job]:
        s = self.settings.search
        jobs: list[Job] = []
        for page in range(s.linkedin_pages):
            params = {
                "keywords": query,
                "location": location,
                "f_TPR": f"r{s.max_age_hours * 3600}",
                "sortBy": "DD",
                "start": page * PAGE_SIZE,
            }
            resp = await self.get(SEARCH_URL, params=params)
            batch = parse_search_results(resp.text)
            jobs.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return jobs

    async def enrich(self, job: Job) -> Job:
        if job.description or not job.external_id:
            return job
        try:
            resp = await self.get(DETAIL_URL.format(job_id=job.external_id), attempts=2)
        except Exception:
            return job
        description = parse_job_detail(resp.text)
        return job.model_copy(update={"description": description}) if description else job


def parse_search_results(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "lxml")
    jobs: list[Job] = []
    for card in soup.select("div.base-card, div.job-search-card"):
        title_el = card.select_one(".base-search-card__title")
        link_el = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
        if not title_el or not link_el:
            continue

        urn = card.get("data-entity-urn", "")
        match = _JOB_ID_RE.search(urn) or _JOB_ID_RE.search(link_el.get("href", ""))
        job_id = match.group(1) if match else ""

        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        time_el = card.select_one("time")

        posted_at = None
        if time_el and time_el.get("datetime"):
            try:
                posted_at = datetime.fromisoformat(time_el["datetime"])
            except ValueError:
                pass

        url = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else link_el["href"].split("?")[0]
        jobs.append(
            Job(
                source=Source.LINKEDIN,
                title=title_el.get_text(),
                url=url,
                company=company_el.get_text() if company_el else "",
                location=location_el.get_text() if location_el else "",
                posted_at=posted_at,
                external_id=job_id,
            )
        )
    return jobs


def parse_job_detail(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    if not body:
        return ""
    return body.get_text("\n", strip=True)
