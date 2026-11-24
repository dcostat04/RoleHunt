from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod

import httpx

from job_scout.config import Settings
from job_scout.models import Job, ScrapeResult, Source

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
]

RETRY_STATUSES = {429, 500, 502, 503, 504}


class SourceSkipped(Exception):
    """Raised when a source cannot run (e.g. missing credentials). Not an error."""


class Scraper(ABC):
    source: Source

    def __init__(self, client: httpx.AsyncClient, settings: Settings, semaphore: asyncio.Semaphore):
        self.client = client
        self.settings = settings
        self.semaphore = semaphore

    @abstractmethod
    async def scrape(self) -> list[Job]:
        """Return raw jobs. Raise SourceSkipped to skip cleanly."""

    async def enrich(self, job: Job) -> Job:
        """Optionally fetch extra detail (e.g. full description) for a job that
        survived the cheap filters. Default: nothing to do."""
        return job

    async def run(self) -> ScrapeResult:
        try:
            jobs = await self.scrape()
            log.info("%s: scraped %d jobs", self.source, len(jobs))
            return ScrapeResult(source=self.source, jobs=jobs)
        except SourceSkipped as exc:
            log.info("%s: skipped (%s)", self.source, exc)
            return ScrapeResult(source=self.source, skipped=str(exc))
        except httpx.HTTPError as exc:  # blocked / down: expected, no traceback
            log.warning("%s: failed: %s", self.source, exc)
            return ScrapeResult(source=self.source, error=f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # one broken source must never sink the run
            log.exception("%s: failed", self.source)
            return ScrapeResult(source=self.source, error=f"{type(exc).__name__}: {exc}")

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        attempts: int = 3,
    ) -> httpx.Response:
        """GET with bounded concurrency, a browser-ish UA and retry on 429/5xx."""
        merged = {"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "en-US,en;q=0.9"}
        merged.update(headers or {})
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with self.semaphore:
                    resp = await self.client.get(url, params=params, headers=merged)
                if resp.status_code in RETRY_STATUSES and attempt < attempts:
                    delay = _retry_delay(resp, attempt)
                    log.debug("%s: %s -> %d, retrying in %.1fs", self.source, url, resp.status_code, delay)
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
            except (httpx.TransportError,) as exc:
                last_exc = exc
                if attempt < attempts:
                    await asyncio.sleep(_retry_delay(None, attempt))
                    continue
                raise
        raise last_exc or RuntimeError("unreachable")


def _retry_delay(resp: httpx.Response | None, attempt: int) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(float(retry_after), 30.0)
    return min(2 ** attempt + random.random(), 20.0)
