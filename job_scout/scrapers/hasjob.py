"""HasJob (hasjob.co) via its public Atom feed."""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from job_scout.models import Job, Source
from job_scout.scrapers.base import Scraper

FEED_URL = "https://hasjob.co/feed"

# HasJob headlines often end with the location in brackets/parentheses, and
# the HTML body usually carries a "Location:" line.
_TRAILING_LOCATION = re.compile(r"[\(\[]([^\)\]]{2,80})[\)\]]\s*$")
_LOCATION_LINE = re.compile(r"^\s*Location[s]?\s*[:\-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_ID_RE = re.compile(r"/([a-z0-9]{4,})/?$", re.IGNORECASE)


class HasJobScraper(Scraper):
    source = Source.HASJOB

    async def scrape(self) -> list[Job]:
        resp = await self.get(FEED_URL, headers={"Accept": "application/atom+xml,application/xml"})
        return parse_feed(resp.text)


def parse_feed(xml: str) -> list[Job]:
    soup = BeautifulSoup(xml, "xml")
    jobs: list[Job] = []
    for entry in soup.find_all("entry"):
        title = _text(entry.find("title"))
        link_el = entry.find("link", rel="alternate") or entry.find("link")
        url = (link_el.get("href") if link_el else "") or _text(entry.find("id"))
        if not title or not url:
            continue

        content_el = entry.find("content") or entry.find("summary")
        body = BeautifulSoup(content_el.get_text(), "lxml").get_text("\n", strip=True) if content_el else ""

        location = ""
        if m := _LOCATION_LINE.search(body):
            location = m.group(1)
        elif m := _TRAILING_LOCATION.search(title):
            location = m.group(1)

        company = _text(entry.find("author") and entry.find("author").find("name"))
        if not company and " at " in title:
            company = _TRAILING_LOCATION.sub("", title.rsplit(" at ", 1)[1]).strip()

        posted_at = None
        stamp = _text(entry.find("published")) or _text(entry.find("updated"))
        if stamp:
            try:
                posted_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                pass

        id_match = _ID_RE.search(url)
        jobs.append(
            Job(
                source=Source.HASJOB,
                title=title,
                company=company,
                location=location,
                description=body,
                url=url,
                posted_at=posted_at,
                external_id=id_match.group(1) if id_match else "",
            )
        )
    return jobs


def _text(el) -> str:
    return el.get_text(strip=True) if el else ""
