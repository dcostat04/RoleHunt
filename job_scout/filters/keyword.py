"""Stage 1: cheap keyword pre-filter. Drops most irrelevant listings before any
network-heavy enrichment or LLM calls."""

from __future__ import annotations

import re

from job_scout.config import Keywords
from job_scout.models import Job, Source

# Sources without a real job title (HN comments, tweets). A title-only check
# would miss them, and their headers often list several roles at once
# ("Senior Engineer, Product Manager"), so excludes are not applied either;
# the LLM sorts those out.
UNSTRUCTURED = {Source.HACKERNEWS, Source.TWITTER}


def _compile(terms: list[str]) -> re.Pattern[str] | None:
    cleaned = [t.strip().lower() for t in terms if t.strip()]
    if not cleaned:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(t) for t in cleaned) + r")\b", re.IGNORECASE)


class KeywordFilter:
    def __init__(self, config: Keywords):
        self.include = _compile(config.include)
        self.exclude = _compile(config.exclude)

    def matches(self, job: Job) -> bool:
        if job.source in UNSTRUCTURED:
            return self.include is None or bool(self.include.search(job.text_blob))
        title = job.title.lower()
        if self.include is not None and not self.include.search(title):
            return False
        if self.exclude is not None and self.exclude.search(title):
            return False
        return True

    def apply(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if self.matches(j)]
