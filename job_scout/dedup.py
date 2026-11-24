"""Triple-layered deduplication.

1. URL-based:    the same posting scraped twice (multiple LinkedIn queries
                 return overlapping results) collapses to one, after stripping
                 tracking parameters from URLs.
2. Cross-source: the same role posted on LinkedIn *and* Wellfound *and* HN is
                 matched on a normalized company + title fingerprint; the copy
                 from the best source is kept and the others are noted.
3. Historical:   anything already processed in a previous run (stored in a
                 JSON file committed back by the GitHub Action) is dropped.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from job_scout.models import SOURCE_PRIORITY, Job

log = logging.getLogger(__name__)

TRACKING_PARAMS = {
    "ref", "refid", "trackingid", "trk", "trkinfo", "src", "source", "origin", "position",
    "pagenum", "lipi", "eBP", "fbclid", "gclid", "mc_cid", "mc_eid", "s", "t", "si",
}
# Query params that actually identify a page and must be kept.
IDENTITY_PARAMS = {"id", "currentjobid", "jk", "gh_jid"}

_LINKEDIN_ID = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?]*?-)?(\d{6,})")
_WELLFOUND_ID = re.compile(r"wellfound\.com/jobs/(\d+)")
_X_STATUS = re.compile(r"(?:x|twitter)\.com/[^/]+/status/(\d+)")


def normalize_url(url: str) -> str:
    """Canonical form of a job URL for equality checks."""
    url = url.strip()
    if m := _LINKEDIN_ID.search(url):
        return f"linkedin.com/jobs/view/{m.group(1)}"
    if m := _WELLFOUND_ID.search(url):
        return f"wellfound.com/jobs/{m.group(1)}"
    if m := _X_STATUS.search(url):
        return f"x.com/status/{m.group(1)}"

    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parts.path.rstrip("/") or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() in IDENTITY_PARAMS
        or (k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_"))
    ]
    return urlunsplit(("", host, path, urlencode(sorted(query)), "")).lstrip("/")


_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|co|gmbh|technologies|technology|"
    r"tech|labs|software|solutions|india|hq|ai)\b"
)
_TITLE_NOISE = re.compile(r"\b(remote|hybrid|onsite|on site|full time|full-time|contract|urgent|hiring)\b")
_TITLE_ABBREV = {
    r"\bsr\b": "senior",
    r"\bjr\b": "junior",
    r"\bpm\b": "product manager",
    r"\bapm\b": "associate product manager",
    r"\bmgr\b": "manager",
}


def _norm_company(company: str) -> str:
    s = company.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _COMPANY_SUFFIXES.sub(" ", s)
    return "".join(s.split())


def _norm_title(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)          # "(Remote)", "[Bengaluru]"
    s = re.split(r"\s[-–—|@]\s|,", s)[0]           # drop " - Payments team", ", Growth"
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    for pattern, repl in _TITLE_ABBREV.items():
        s = re.sub(pattern, repl, s)
    s = _TITLE_NOISE.sub(" ", s)
    return " ".join(s.split())


def fingerprint(job: Job) -> str | None:
    """company|title key for cross-source matching, or None when the job lacks
    a reliable company/title (tweets, HN headers that are not structured)."""
    company = _norm_company(job.company)
    title = _norm_title(job.title)
    if not company or not title or len(title) > 80:
        return None
    return f"{company}|{title}"


def dedup_by_url(jobs: list[Job]) -> list[Job]:
    seen: dict[str, Job] = {}
    for job in jobs:
        key = normalize_url(job.url)
        existing = seen.get(key)
        # Keep whichever copy carries more information.
        if existing is None or len(job.description) > len(existing.description):
            seen[key] = job
    return list(seen.values())


def dedup_cross_source(jobs: list[Job]) -> list[Job]:
    groups: dict[str, list[Job]] = {}
    out: list[Job] = []
    for job in jobs:
        key = fingerprint(job)
        if key is None:
            out.append(job)
        else:
            groups.setdefault(key, []).append(job)

    for group in groups.values():
        group.sort(key=lambda j: (SOURCE_PRIORITY[j.source], -len(j.description)))
        best = group[0]
        others = sorted({j.source for j in group[1:] if j.source != best.source}, key=SOURCE_PRIORITY.get)
        if others:
            best = best.model_copy(update={"also_seen_on": others})
        out.append(best)
    return out


class SeenStore:
    """Persistent record of jobs already processed, keyed by canonical URL and
    by cross-source fingerprint, with timestamps so old entries can expire."""

    def __init__(self, path: Path, retention_days: int):
        self.path = Path(path)
        self.retention = timedelta(days=retention_days)
        self.entries: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.entries = dict(data.get("seen", {}))
        except (json.JSONDecodeError, OSError, AttributeError) as exc:
            log.warning("Could not read %s (%s); starting with empty history", self.path, exc)
            self.entries = {}

    @staticmethod
    def keys_for(job: Job) -> list[str]:
        keys = [f"url:{normalize_url(job.url)}"]
        if fp := fingerprint(job):
            keys.append(f"fp:{fp}")
        return keys

    def is_seen(self, job: Job) -> bool:
        return any(k in self.entries for k in self.keys_for(job))

    def filter_unseen(self, jobs: list[Job]) -> list[Job]:
        return [j for j in jobs if not self.is_seen(j)]

    def mark(self, jobs: list[Job], now: datetime | None = None) -> None:
        stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        for job in jobs:
            for key in self.keys_for(job):
                self.entries.setdefault(key, stamp)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(timezone.utc)) - self.retention
        before = len(self.entries)
        kept = {}
        for key, stamp in self.entries.items():
            try:
                if datetime.fromisoformat(stamp) >= cutoff:
                    kept[key] = stamp
            except ValueError:
                continue
        self.entries = kept
        return before - len(kept)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "count": len(self.entries), "seen": dict(sorted(self.entries.items()))}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1) + "\n")
        tmp.replace(self.path)
