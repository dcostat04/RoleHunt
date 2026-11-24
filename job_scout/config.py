from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Sources(BaseModel):
    linkedin: bool = True
    hackernews: bool = True
    twitter: bool = True
    wellfound: bool = True
    hasjob: bool = True


class Search(BaseModel):
    queries: list[str] = ["product manager"]
    linkedin_locations: list[str] = ["India"]
    max_age_hours: int = 24
    linkedin_pages: int = 3
    wellfound_role: str = "product-manager"
    wellfound_locations: list[str] = ["india", "remote"]


class Keywords(BaseModel):
    include: list[str] = ["product manager"]
    exclude: list[str] = []


class Locations(BaseModel):
    allow: list[str] = ["remote", "india"]
    deny: list[str] = []
    allow_unknown: bool = True


class LLM(BaseModel):
    model: str = "gemini-2.5-flash"
    min_score: float = 7
    batch_size: int = 10
    concurrency: int = 3
    fail_open: bool = True


class Dedup(BaseModel):
    history_file: Path = Path("data/seen_jobs.json")
    retention_days: int = 45


class Email(BaseModel):
    subject_prefix: str = "Job Scout"
    max_jobs: int = 50


class HTTP(BaseModel):
    timeout_seconds: float = 25
    max_concurrency: int = 8


class Secrets(BaseModel):
    """Read from the environment only."""

    gemini_api_key: str | None = None
    x_bearer_token: str | None = None
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    email_to: list[str] = Field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Secrets":
        def env(name: str) -> str | None:
            value = os.environ.get(name, "").strip()
            return value or None

        to = env("EMAIL_TO") or ""
        return cls(
            gemini_api_key=env("GEMINI_API_KEY"),
            x_bearer_token=env("X_BEARER_TOKEN"),
            smtp_host=env("SMTP_HOST") or "smtp.gmail.com",
            smtp_port=int(env("SMTP_PORT") or 587),
            smtp_user=env("SMTP_USER"),
            smtp_password=env("SMTP_PASSWORD"),
            email_from=env("EMAIL_FROM") or env("SMTP_USER"),
            email_to=[addr.strip() for addr in to.split(",") if addr.strip()],
        )


class Settings(BaseModel):
    profile: str = ""
    sources: Sources = Sources()
    search: Search = Search()
    keywords: Keywords = Keywords()
    locations: Locations = Locations()
    llm: LLM = LLM()
    dedup: Dedup = Dedup()
    email: Email = Email()
    http: HTTP = HTTP()
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Settings":
        path = Path(path)
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        settings = cls.model_validate(data or {})
        settings.secrets = Secrets.from_env()
        return settings
