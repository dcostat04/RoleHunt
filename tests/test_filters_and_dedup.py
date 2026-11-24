from datetime import datetime, timedelta, timezone

import pytest

from job_scout.config import Keywords, Locations
from job_scout.dedup import (
    SeenStore,
    dedup_by_url,
    dedup_cross_source,
    fingerprint,
    normalize_url,
)
from job_scout.filters.keyword import KeywordFilter
from job_scout.filters.llm import build_prompt, parse_response
from job_scout.filters.location import LocationFilter
from job_scout.models import Job, Source


def job(title, source=Source.LINKEDIN, url=None, company="Acme", location="", description=""):
    return Job(
        source=source, title=title, company=company, location=location, description=description,
        url=url or f"https://example.com/{abs(hash((title, source, company)))}",
    )


# ---- keyword ---------------------------------------------------------------

KW = KeywordFilter(Keywords(include=["product manager", "pm", "head of product"],
                            exclude=["intern", "project manager", "product marketing", "engineer", "vp"]))


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Product Manager", True),
        ("Sr. PM - Growth", True),
        ("Head of Product", True),
        ("Product Manager Intern", False),
        ("Project Manager", False),
        ("Product Marketing Manager", False),
        ("Software Engineer", False),
        ("VP Product", False),
        ("Product Manager, Engineering Platform", True),  # "engineering" != "engineer"
        ("Data Analyst", False),
    ],
)
def test_keyword_filter_titles(title, expected):
    assert KW.matches(job(title)) is expected


def test_keyword_filter_unstructured_sources_search_body_and_skip_excludes():
    hn = job("Acme | Senior Engineer, Product Manager | Remote", source=Source.HACKERNEWS)
    assert KW.matches(hn)
    tweet = job("We're hiring!", source=Source.TWITTER, description="Looking for a product manager in Pune")
    assert KW.matches(tweet)
    assert not KW.matches(job("We're hiring!", source=Source.TWITTER, description="Backend roles"))


# ---- location ---------------------------------------------------------------

LOC = LocationFilter(Locations(allow=["remote", "india", "bengaluru", "bangalore"], deny=["us only"], allow_unknown=True))


def test_location_filter():
    assert LOC.matches(job("PM", location="Bengaluru, Karnataka, India"))
    assert not LOC.matches(job("PM", location="San Francisco, CA"))
    assert LOC.matches(job("Product Manager (Remote)", location="San Francisco, CA"))
    assert LOC.matches(job("PM", location=""))  # unknown allowed
    assert not LOC.matches(job("PM", location="Remote", description="Remote, US only"))
    strict = LocationFilter(Locations(allow=["india"], allow_unknown=False))
    assert not strict.matches(job("PM"))
    assert strict.matches(job("PM", source=Source.TWITTER, description="Hiring in India"))


# ---- dedup ------------------------------------------------------------------

def test_normalize_url():
    a = "https://in.linkedin.com/jobs/view/senior-product-manager-at-razorpay-4012345678?position=1&refId=abc"
    b = "https://www.linkedin.com/jobs/view/4012345678/"
    assert normalize_url(a) == normalize_url(b) == "linkedin.com/jobs/view/4012345678"
    assert normalize_url("https://news.ycombinator.com/item?id=1&utm_source=x") == "news.ycombinator.com/item?id=1"
    assert normalize_url("https://www.Example.com/jobs/1/?utm_medium=e&ref=tw&team=pm") == "example.com/jobs/1?team=pm"
    assert normalize_url("https://twitter.com/a/status/99?s=20") == normalize_url("https://x.com/b/status/99")


def test_dedup_by_url_keeps_richest_copy():
    a = job("PM", url="https://www.linkedin.com/jobs/view/123456789?trk=a")
    b = job("PM", url="https://in.linkedin.com/jobs/view/pm-at-acme-123456789", description="full text")
    out = dedup_by_url([a, b])
    assert len(out) == 1 and out[0].description == "full text"


def test_fingerprint_normalization():
    a = job("Sr. Product Manager (Remote)", company="Razorpay Software Pvt Ltd")
    b = job("Senior PM - Payments", company="Razorpay")
    assert fingerprint(a) == fingerprint(b) == "razorpay|senior product manager"
    assert fingerprint(job("PM", company="")) is None


def test_cross_source_prefers_best_source_and_records_others():
    li = job("Senior Product Manager", source=Source.LINKEDIN, company="Razorpay", url="https://linkedin.com/jobs/view/1111111")
    wf = job("Senior Product Manager", source=Source.WELLFOUND, company="Razorpay Pvt Ltd", url="https://wellfound.com/jobs/1")
    other = job("Product Manager", source=Source.HASJOB, company="Zerodha", url="https://hasjob.co/x/1")
    tweet = job("hiring!", source=Source.TWITTER, company="", url="https://x.com/a/status/1")
    out = dedup_cross_source([wf, li, other, tweet])
    assert len(out) == 3
    kept = next(j for j in out if j.company.startswith("Razorpay"))
    assert kept.source is Source.LINKEDIN
    assert kept.also_seen_on == [Source.WELLFOUND]


def test_seen_store_roundtrip_and_prune(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenStore(path, retention_days=30)
    old, new = job("PM", url="https://a.com/1", company="A"), job("PM", url="https://b.com/2", company="B")
    now = datetime.now(timezone.utc)
    store.mark([old], now=now - timedelta(days=40))
    store.mark([new], now=now)
    assert store.prune(now=now) == 2  # url + fingerprint keys of `old`
    store.save()

    reloaded = SeenStore(path, retention_days=30)
    assert reloaded.is_seen(new)
    assert not reloaded.is_seen(old)
    # Same company/title from another source counts as seen via fingerprint.
    assert reloaded.is_seen(job("PM", source=Source.HASJOB, url="https://hasjob.co/z/9", company="B Labs"))


def test_seen_store_tolerates_corrupt_file(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not json")
    assert SeenStore(path, 30).entries == {}


# ---- LLM prompt/response ----------------------------------------------------

def test_llm_prompt_and_response_parsing():
    prompt = build_prompt("I am a PM", [job("Senior PM", description="x" * 5000)])
    assert "I am a PM" in prompt and '"id": "0"' in prompt
    assert "x" * 1801 not in prompt  # description truncated

    payload = {
        "candidates": [{
            "content": {"parts": [
                {"text": "thinking...", "thought": True},
                {"text": '[{"id": "0", "score": 8.5, "reason": "Good fit"}]'},
            ]},
            "finishReason": "STOP",
        }]
    }
    scores = parse_response(payload)
    assert scores[0].id == "0" and scores[0].score == 8.5
