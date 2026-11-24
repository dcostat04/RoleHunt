"""Stage 2: location matching."""

from __future__ import annotations

import re

from job_scout.config import Locations
from job_scout.models import Job


def _compile(terms: list[str]) -> re.Pattern[str] | None:
    cleaned = [t.strip().lower() for t in terms if t.strip()]
    if not cleaned:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in cleaned) + r")\b", re.IGNORECASE)


class LocationFilter:
    def __init__(self, config: Locations):
        self.allow = _compile(config.allow)
        self.deny = _compile(config.deny)
        self.allow_unknown = config.allow_unknown

    def matches(self, job: Job) -> bool:
        if self.deny is not None and self.deny.search(job.text_blob):
            return False
        if self.allow is None:
            return True
        # A structured location is authoritative; the title is checked too
        # because postings like "Product Manager (Remote)" list an HQ city.
        if job.location:
            return bool(self.allow.search(f"{job.location} {job.title}"))
        # No structured location (tweets, some HN/HasJob posts): search free text.
        if self.allow.search(job.text_blob):
            return True
        return self.allow_unknown

    def apply(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if self.matches(j)]
