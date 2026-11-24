"""X / Twitter via the official v2 recent-search API.

X no longer allows meaningful logged-out scraping, so this source needs an
X API bearer token (X_BEARER_TOKEN) on a tier that includes recent search.
Without one the source is skipped, not failed.
"""

from __future__ import annotations

from datetime import datetime

from job_scout.models import Job, Source
from job_scout.scrapers.base import Scraper, SourceSkipped

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


def build_query(queries: list[str]) -> str:
    roles = " OR ".join(f'"{q}"' for q in queries)
    return f'({roles}) (hiring OR "we\'re hiring" OR "join us" OR "open role") -is:retweet -is:reply lang:en'


class TwitterScraper(Scraper):
    source = Source.TWITTER

    async def scrape(self) -> list[Job]:
        token = self.settings.secrets.x_bearer_token
        if not token:
            raise SourceSkipped("X_BEARER_TOKEN not set")

        params = {
            "query": build_query(self.settings.search.queries),
            "max_results": 100,
            "tweet.fields": "created_at,author_id,entities",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        resp = await self.get(SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"})
        return parse_search_response(resp.json())


def parse_search_response(payload: dict) -> list[Job]:
    users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}
    jobs: list[Job] = []
    for tweet in payload.get("data", []):
        user = users.get(tweet.get("author_id"), {})
        username = user.get("username", "i")
        text: str = tweet.get("text", "")

        # Prefer an expanded external link (often the actual job posting) for the description.
        links = [
            u.get("expanded_url", "")
            for u in tweet.get("entities", {}).get("urls", [])
            if "x.com" not in u.get("expanded_url", "") and "twitter.com" not in u.get("expanded_url", "")
        ]
        description = text + ("\n\nLinks: " + " ".join(links) if links else "")

        posted_at = None
        if tweet.get("created_at"):
            try:
                posted_at = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
            except ValueError:
                pass

        first_line = text.strip().splitlines()[0] if text.strip() else ""
        jobs.append(
            Job(
                source=Source.TWITTER,
                title=first_line[:140],
                company=user.get("name", ""),
                description=description,
                url=f"https://x.com/{username}/status/{tweet['id']}",
                posted_at=posted_at,
                external_id=str(tweet["id"]),
            )
        )
    return jobs
