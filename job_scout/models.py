from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Source(StrEnum):
    LINKEDIN = "linkedin"
    HACKERNEWS = "hackernews"
    TWITTER = "twitter"
    WELLFOUND = "wellfound"
    HASJOB = "hasjob"


# When the same job shows up in several sources, keep the one from the
# source with the richest structured data.
SOURCE_PRIORITY: dict[Source, int] = {
    Source.LINKEDIN: 0,
    Source.WELLFOUND: 1,
    Source.HASJOB: 2,
    Source.HACKERNEWS: 3,
    Source.TWITTER: 4,
}


class Job(BaseModel):
    source: Source
    title: str
    url: str
    company: str = ""
    location: str = ""
    description: str = ""
    posted_at: datetime | None = None
    # Source-specific id (LinkedIn job id, HN comment id, tweet id...)
    external_id: str = ""

    # Filled in by the pipeline
    score: float | None = None
    reason: str = ""
    also_seen_on: list[Source] = Field(default_factory=list)

    @field_validator("title", "company", "location", mode="before")
    @classmethod
    def _squash_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return " ".join(v.split())
        return v

    @field_validator("description", mode="before")
    @classmethod
    def _strip_description(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    @property
    def text_blob(self) -> str:
        """Everything we know about the job, lowercased, for matching."""
        return f"{self.title}\n{self.company}\n{self.location}\n{self.description}".lower()


class ScrapeResult(BaseModel):
    source: Source
    jobs: list[Job] = Field(default_factory=list)
    error: str | None = None
    skipped: str | None = None


class RunStats(BaseModel):
    scraped: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    skipped: dict[str, str] = Field(default_factory=dict)
    after_url_dedup: int = 0
    after_history: int = 0
    after_cross_source: int = 0
    after_keyword: int = 0
    after_location: int = 0
    after_llm: int = 0
    llm_scored: bool = False
