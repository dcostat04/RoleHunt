"""Render the email digest (HTML + plain-text alternative)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

from job_scout.models import Job, RunStats, Source

IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_LABEL = {
    Source.LINKEDIN: ("LinkedIn", "#0a66c2"),
    Source.HACKERNEWS: ("Hacker News", "#ff6600"),
    Source.TWITTER: ("X", "#111111"),
    Source.WELLFOUND: ("Wellfound", "#c7254e"),
    Source.HASJOB: ("HasJob", "#2e7d32"),
}


def sort_jobs(jobs: list[Job]) -> list[Job]:
    # Scored jobs first (highest score), then unscored, newest first within ties.
    def key(j: Job):
        posted = j.posted_at.timestamp() if j.posted_at else 0
        return (j.score is None, -(j.score or 0), -posted)

    return sorted(jobs, key=key)


def subject(prefix: str, jobs: list[Job], now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    slot = "Morning" if now.hour < 13 else "Evening"
    n = len(jobs)
    return f"{prefix}: {n} new PM role{'s' if n != 1 else ''} · {slot} {now:%d %b}"


def render_text(jobs: list[Job], stats: RunStats) -> str:
    lines = [f"{len(jobs)} new matching roles", ""]
    for job in jobs:
        score = f"[{job.score:.0f}/10] " if job.score is not None else ""
        where = " · ".join(p for p in (job.company, job.location) if p)
        lines.append(f"{score}{job.title}")
        if where:
            lines.append(f"  {where}")
        if job.reason:
            lines.append(f"  Why: {job.reason}")
        lines.append(f"  {SOURCE_LABEL[job.source][0]}: {job.url}")
        lines.append("")
    lines.append(_stats_line(stats))
    return "\n".join(lines)


def render_html(jobs: list[Job], stats: RunStats) -> str:
    cards = "\n".join(_card(j) for j in jobs) or (
        '<p style="color:#555">No new matching roles this run.</p>'
    )
    errors = ""
    if stats.errors:
        items = "".join(f"<li><b>{escape(k)}</b>: {escape(v[:160])}</li>" for k, v in stats.errors.items())
        errors = f'<div style="margin-top:16px;color:#8a1c1c;font-size:12px">Source errors:<ul>{items}</ul></div>'

    unscored_note = ""
    if not stats.llm_scored:
        unscored_note = (
            '<p style="background:#fff4e5;padding:8px 12px;border-radius:6px;font-size:13px">'
            "LLM scoring was unavailable this run; showing keyword and location matches only.</p>"
        )

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f1ec">
<div style="max-width:640px;margin:0 auto;padding:24px 16px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#2b2118">
  <h1 style="font-size:22px;margin:0 0 4px">Job Scout</h1>
  <p style="margin:0 0 20px;color:#6b5d50;font-size:14px">{len(jobs)} new role{'s' if len(jobs) != 1 else ''} worth a look</p>
  {unscored_note}
  {cards}
  <p style="margin-top:24px;color:#8c7f73;font-size:12px">{escape(_stats_line(stats))}</p>
  {errors}
</div></body></html>"""


def _card(job: Job) -> str:
    label, color = SOURCE_LABEL[job.source]
    badge = (
        f'<span style="display:inline-block;background:{color};color:#fff;font-size:11px;'
        f'padding:2px 8px;border-radius:10px;margin-right:6px">{label}</span>'
    )
    also = ""
    if job.also_seen_on:
        names = ", ".join(SOURCE_LABEL[s][0] for s in job.also_seen_on)
        also = f'<span style="font-size:11px;color:#8c7f73">also on {escape(names)}</span>'

    score = ""
    if job.score is not None:
        bg = "#1b7f3b" if job.score >= 9 else "#3b7d9c" if job.score >= 7 else "#8c7f73"
        score = (
            f'<span style="float:right;background:{bg};color:#fff;font-weight:600;font-size:13px;'
            f'padding:2px 8px;border-radius:6px">{job.score:.0f}/10</span>'
        )

    meta = " · ".join(escape(p) for p in (job.company, job.location) if p)
    posted = ""
    if job.posted_at:
        posted = f" · {job.posted_at.astimezone(IST):%d %b}"
    reason = (
        f'<p style="margin:8px 0 0;font-size:13px;color:#4a3f35">{escape(job.reason)}</p>' if job.reason else ""
    )
    return f"""<div style="background:#fff;border:1px solid #e6dfd6;border-radius:10px;padding:14px 16px;margin-bottom:12px">
    {score}
    <a href="{escape(job.url, quote=True)}" style="font-size:16px;font-weight:600;color:#2b2118;text-decoration:none">{escape(job.title)}</a>
    <div style="margin-top:4px;font-size:13px;color:#6b5d50">{meta}{posted}</div>
    {reason}
    <div style="margin-top:10px">{badge}{also}</div>
  </div>"""


def _stats_line(stats: RunStats) -> str:
    total = sum(stats.scraped.values())
    per_source = ", ".join(f"{k} {v}" for k, v in stats.scraped.items())
    return (
        f"Scraped {total} ({per_source}) → {stats.after_history} new → "
        f"{stats.after_keyword} keyword → {stats.after_location} location → {stats.after_llm} LLM."
    )
