"""Stage 3: Gemini Flash relevance scoring.

Jobs are scored in batches against the candidate profile from config.yaml.
Uses the Gemini REST API directly with structured JSON output, so there is no
SDK dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from pydantic import BaseModel, ValidationError

from job_scout.config import LLM
from job_scout.models import Job

log = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DESCRIPTION_CHARS = 1800

SYSTEM_PROMPT = """You are a strict recruiter screening job listings for one specific candidate.
Score each listing from 0 to 10 for how well it fits the candidate profile:
  9-10  clearly a product management role the candidate should apply to today
  7-8   good fit with minor mismatches (seniority, domain, location ambiguity)
  4-6   product-adjacent or significant mismatch
  0-3   not a PM role, wrong location, wrong seniority, or not actually a job posting
Hacker News comments and tweets may mention several roles; score the best-matching role
and name it in the reason. Treat location and seniority requirements in the profile as hard
constraints. Keep each reason under 25 words and make it specific to the listing."""

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "score": {"type": "NUMBER"},
            "reason": {"type": "STRING"},
        },
        "required": ["id", "score", "reason"],
    },
}


class LLMScore(BaseModel):
    id: str
    score: float
    reason: str


class LLMError(Exception):
    pass


class LLMScorer:
    def __init__(self, client: httpx.AsyncClient, config: LLM, api_key: str, profile: str):
        self.client = client
        self.config = config
        self.api_key = api_key
        self.profile = profile
        self.semaphore = asyncio.Semaphore(config.concurrency)

    async def score(self, jobs: list[Job]) -> list[Job]:
        """Return every job with `score`/`reason` set. Jobs whose batch failed
        are returned with score=None when fail_open is on, dropped otherwise."""
        size = max(1, self.config.batch_size)
        batches = [jobs[i : i + size] for i in range(0, len(jobs), size)]
        results = await asyncio.gather(*(self._score_batch(b) for b in batches))
        return [job for batch in results for job in batch]

    def keep(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if j.score is None or j.score >= self.config.min_score]

    async def _score_batch(self, batch: list[Job]) -> list[Job]:
        try:
            async with self.semaphore:
                scores = await self._call(batch)
        except Exception as exc:
            log.warning("LLM batch of %d failed: %s", len(batch), exc)
            if not self.config.fail_open:
                return []
            return [j.model_copy(update={"score": None, "reason": "Not scored (LLM error)"}) for j in batch]

        by_id = {s.id: s for s in scores}
        scored: list[Job] = []
        for idx, job in enumerate(batch):
            s = by_id.get(str(idx))
            if s is None:
                if self.config.fail_open:
                    scored.append(job.model_copy(update={"score": None, "reason": "Not scored (missing from LLM reply)"}))
                continue
            scored.append(job.model_copy(update={"score": max(0.0, min(10.0, s.score)), "reason": s.reason}))
        return scored

    async def _call(self, batch: list[Job], attempts: int = 4) -> list[LLMScore]:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": build_prompt(self.profile, batch)}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        }
        url = API_URL.format(model=self.config.model)
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

        for attempt in range(1, attempts + 1):
            try:
                resp = await self.client.post(url, json=body, headers=headers, timeout=90)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise LLMError(f"transport error: {exc}") from exc
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < attempts:
                await asyncio.sleep(min(2 ** (attempt + 1), 30))
                continue
            if resp.status_code != 200:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return parse_response(resp.json())
            except (LLMError, ValidationError, json.JSONDecodeError) as exc:
                if attempt == attempts:
                    raise LLMError(f"bad response: {exc}") from exc
        raise LLMError("exhausted retries")


def build_prompt(profile: str, batch: list[Job]) -> str:
    listings = []
    for idx, job in enumerate(batch):
        listings.append(
            {
                "id": str(idx),
                "source": job.source.value,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "description": job.description[:DESCRIPTION_CHARS],
            }
        )
    return (
        f"CANDIDATE PROFILE:\n{profile.strip()}\n\n"
        f"LISTINGS (JSON):\n{json.dumps(listings, ensure_ascii=False, indent=1)}\n\n"
        "Return one object per listing with its id, score and reason."
    )


def parse_response(payload: dict) -> list[LLMScore]:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError(f"no candidates (promptFeedback={payload.get('promptFeedback')})")
    parts = candidates[0].get("content", {}).get("parts", [])
    # Skip any thought parts some models return; the answer is the non-thought text.
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise LLMError(f"empty response (finishReason={candidates[0].get('finishReason')})")
    data = json.loads(text)
    if isinstance(data, dict):  # tolerate {"results": [...]}
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [LLMScore.model_validate(item) for item in data]
