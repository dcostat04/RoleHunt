"""End-to-end run: scrape -> dedup -> filter -> score -> email -> remember."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from job_scout import digest
from job_scout.config import Settings
from job_scout.dedup import SeenStore, dedup_by_url, dedup_cross_source, normalize_url
from job_scout.emailer import send_email
from job_scout.filters import KeywordFilter, LLMScorer, LocationFilter
from job_scout.models import Job, RunStats, Source
from job_scout.scrapers import SCRAPERS, Scraper

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    dry_run: bool = False                 # don't send email or persist history
    use_llm: bool = True
    send_empty: bool = False              # send an email even when nothing matched
    sources: set[Source] | None = None    # restrict to these sources
    preview_path: Path | None = Path("out/digest.html")


@dataclass
class RunResult:
    jobs: list[Job]
    stats: RunStats
    emailed: bool = False
    all_sources_failed: bool = False
    seen_saved: bool = False
    notes: list[str] = field(default_factory=list)


async def run(settings: Settings, options: RunOptions | None = None) -> RunResult:
    options = options or RunOptions()
    stats = RunStats()

    limits = httpx.Limits(max_connections=settings.http.max_concurrency * 2)
    timeout = httpx.Timeout(settings.http.timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True, http2=False) as client:
        semaphore = asyncio.Semaphore(settings.http.max_concurrency)
        scrapers = _build_scrapers(client, settings, semaphore, options.sources)

        # --- Scrape every source concurrently ----------------------------------
        results = await asyncio.gather(*(s.run() for s in scrapers.values()))
        raw: list[Job] = []
        for r in results:
            stats.scraped[r.source.value] = len(r.jobs)
            if r.error:
                stats.errors[r.source.value] = r.error
            if r.skipped:
                stats.skipped[r.source.value] = r.skipped
            raw.extend(r.jobs)
        attempted = [r for r in results if not r.skipped]
        all_failed = bool(attempted) and all(r.error for r in attempted)

        # --- Dedup layer 1 + 3 + 2 ------------------------------------------------
        jobs = dedup_by_url(raw)
        stats.after_url_dedup = len(jobs)

        store = SeenStore(settings.dedup.history_file, settings.dedup.retention_days)
        jobs = store.filter_unseen(jobs)
        stats.after_history = len(jobs)
        new_this_run = list(jobs)

        jobs = dedup_cross_source(jobs)
        stats.after_cross_source = len(jobs)

        # --- Filter stage 1 + 2 --------------------------------------------------
        jobs = KeywordFilter(settings.keywords).apply(jobs)
        stats.after_keyword = len(jobs)

        location_filter = LocationFilter(settings.locations)
        jobs = location_filter.apply(jobs)

        # Fetch full descriptions only for survivors, then re-check location
        # since a description can reveal e.g. "US only".
        jobs = await _enrich(jobs, scrapers)
        jobs = location_filter.apply(jobs)
        stats.after_location = len(jobs)

        # --- Filter stage 3: LLM ---------------------------------------------------
        unresolved: set[str] = set()
        api_key = settings.secrets.gemini_api_key
        if options.use_llm and api_key and jobs:
            scorer = LLMScorer(client, settings.llm, api_key, settings.profile)
            scored = await scorer.score(jobs)
            stats.llm_scored = any(j.score is not None for j in scored)
            returned = {normalize_url(j.url) for j in scored}
            unresolved = {normalize_url(j.url) for j in jobs} - returned
            unresolved |= {normalize_url(j.url) for j in scored if j.score is None}
            jobs = scorer.keep(scored)
        else:
            if options.use_llm and not api_key:
                log.warning("GEMINI_API_KEY not set: skipping LLM scoring")
            if not settings.llm.fail_open and options.use_llm:
                unresolved = {normalize_url(j.url) for j in jobs}
                jobs = []
        stats.after_llm = len(jobs)

    ranked = digest.sort_jobs(jobs)
    jobs = ranked[: settings.email.max_jobs]
    # Matches beyond the email cap are not marked seen; they carry over.
    unresolved |= {normalize_url(j.url) for j in ranked[settings.email.max_jobs :]}
    result = RunResult(jobs=jobs, stats=stats, all_sources_failed=all_failed)
    log.info("Stats: %s", stats.model_dump_json())

    # --- Email -------------------------------------------------------------------
    subject = digest.subject(settings.email.subject_prefix, jobs)
    html = digest.render_html(jobs, stats)
    text = digest.render_text(jobs, stats)

    if options.preview_path:
        options.preview_path.parent.mkdir(parents=True, exist_ok=True)
        options.preview_path.write_text(html)
        log.info("Wrote preview to %s", options.preview_path)

    if options.dry_run:
        result.notes.append("dry run: no email sent, history not saved")
        return result

    if jobs or options.send_empty:
        await send_email(settings.secrets, subject, text, html)  # raises on failure
        result.emailed = True
        log.info("Emailed %d jobs to %s recipient(s)", len(jobs), len(settings.secrets.email_to))
    else:
        result.notes.append("nothing new matched; no email sent")

    # --- Remember everything processed this run (only after email succeeded) -----
    # Jobs whose LLM scoring never resolved (and weren't emailed anyway) are left
    # unmarked so they get another chance next run.
    emailed = {normalize_url(j.url) for j in jobs}
    to_mark = [
        j for j in new_this_run
        if normalize_url(j.url) not in unresolved or normalize_url(j.url) in emailed
    ]
    store.mark(to_mark)
    pruned = store.prune()
    store.save()
    result.seen_saved = True
    log.info("History: marked %d, pruned %d, total %d", len(to_mark), pruned, len(store.entries))
    return result


def _build_scrapers(
    client: httpx.AsyncClient,
    settings: Settings,
    semaphore: asyncio.Semaphore,
    only: set[Source] | None,
) -> dict[Source, Scraper]:
    enabled = settings.sources.model_dump()
    scrapers: dict[Source, Scraper] = {}
    for source, cls in SCRAPERS.items():
        if only is not None and source not in only:
            continue
        if only is None and not enabled.get(source.value, False):
            continue
        scrapers[source] = cls(client, settings, semaphore)
    return scrapers


async def _enrich(jobs: list[Job], scrapers: dict[Source, Scraper]) -> list[Job]:
    async def one(job: Job) -> Job:
        scraper = scrapers.get(job.source)
        return await scraper.enrich(job) if scraper else job

    return list(await asyncio.gather(*(one(j) for j in jobs)))
