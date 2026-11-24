"""End-to-end run with every HTTP endpoint mocked (respx) and email captured."""

import json
import re

import httpx
import pytest
import respx

from job_scout import pipeline
from job_scout.pipeline import RunOptions

from conftest import HN_ITEM, HN_SEARCH, TWEETS, fixture_text, wellfound_html

LINKEDIN_DETAIL = '<div class="show-more-less-html__markup"><p>Own payments roadmap. 4+ years PM.</p></div>'


def gemini_reply(request: httpx.Request) -> httpx.Response:
    """Fake Gemini: score Senior roles, Plane and Zerodha high."""
    body = json.loads(request.content)
    prompt = body["contents"][0]["parts"][0]["text"]
    listings = json.loads(prompt.split("LISTINGS (JSON):\n", 1)[1].rsplit("\n\nReturn", 1)[0])
    out = []
    for item in listings:
        good = "Senior" in item["title"] or item["company"] in ("Plane", "Zerodha")
        out.append({"id": item["id"], "score": 9 if good else 3, "reason": f"scored {item['title'][:20]}"})
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}]})


def mock_sources(router: respx.Router, gemini=gemini_reply, wellfound_status=200, hasjob_status=200):
    router.get(url__regex=r"https://www\.linkedin\.com/jobs-guest/jobs/api/seeMoreJobPostings/search.*").respond(
        200, text=fixture_text("linkedin_search.html"))
    router.get(url__regex=r"https://www\.linkedin\.com/jobs-guest/jobs/api/jobPosting/\d+").respond(200, text=LINKEDIN_DETAIL)
    router.get(url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*").respond(200, json=HN_SEARCH)
    router.get("https://hn.algolia.com/api/v1/items/45000000").respond(200, json=HN_ITEM)
    router.get(url__regex=r"https://api\.x\.com/2/tweets/search/recent.*").respond(200, json=TWEETS)
    router.get(url__regex=r"https://wellfound\.com/role/l/.*").respond(wellfound_status, text=wellfound_html())
    router.get("https://hasjob.co/feed").respond(hasjob_status, text=fixture_text("hasjob_feed.xml"))
    router.post(url__regex=r"https://generativelanguage\.googleapis\.com/.*:generateContent").mock(side_effect=gemini)


@pytest.fixture
def sent(monkeypatch):
    emails = []

    async def fake_send(secrets, subject, text, html):
        emails.append({"subject": subject, "text": text, "html": html})

    monkeypatch.setattr(pipeline, "send_email", fake_send)
    return emails


async def test_full_run_filters_dedups_and_remembers(settings, sent, tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router)
        result = await pipeline.run(settings, RunOptions(preview_path=tmp_path / "d.html"))

    assert result.stats.errors == {}
    titles = [j.title for j in result.jobs]

    # Razorpay Senior PM appears on LinkedIn, Wellfound and HN -> one entry, from LinkedIn.
    razorpay = [j for j in result.jobs if j.company.startswith("Razorpay")]
    assert len(razorpay) == 1
    assert razorpay[0].source.value == "linkedin"
    assert "wellfound" in razorpay[0].also_seen_on
    assert razorpay[0].description.startswith("Own payments roadmap")  # enriched

    assert any(j.company == "Plane" for j in result.jobs)                  # HN remote PM
    assert any("Zerodha" in j.company for j in result.jobs)     # HasJob
    assert not any("Marketing" in t for t in titles)            # keyword exclude
    assert not any("Stripe" in j.company for j in result.jobs)  # San Francisco -> location filter
    assert not any("Firmware" in t for t in titles)             # keyword
    assert all(j.score is not None and j.score >= 7 for j in result.jobs)

    assert result.emailed and len(sent) == 1
    assert "new PM role" in sent[0]["subject"]
    assert "Razorpay" in sent[0]["html"]

    # Second run: everything is historical -> nothing sent.
    sent.clear()
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router)
        second = await pipeline.run(settings, RunOptions(preview_path=None))
    assert second.jobs == []
    assert sent == []


async def test_source_failure_is_isolated(settings, sent):
    # Wellfound blocked by bot protection, HasJob down.
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router, wellfound_status=403, hasjob_status=503)
        result = await pipeline.run(settings, RunOptions(preview_path=None))

    assert set(result.stats.errors) == {"wellfound", "hasjob"}
    assert not result.all_sources_failed
    assert result.jobs  # LinkedIn/HN still delivered


async def test_llm_outage_fails_open_with_unscored_jobs(settings, sent):
    def broken(request):
        return httpx.Response(400, json={"error": {"message": "API key not valid"}})

    with respx.mock(assert_all_called=False) as router:
        mock_sources(router, gemini=broken)
        result = await pipeline.run(settings, RunOptions(preview_path=None))

    assert result.jobs and all(j.score is None for j in result.jobs)
    assert not result.stats.llm_scored
    assert "LLM scoring was unavailable" in sent[0]["html"]


async def test_dry_run_sends_nothing_and_keeps_history_clean(settings, sent, tmp_path):
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router)
        result = await pipeline.run(settings, RunOptions(dry_run=True, preview_path=tmp_path / "p.html"))
    assert result.jobs and sent == []
    assert not settings.dedup.history_file.exists()
    assert (tmp_path / "p.html").read_text().startswith("<!doctype html>")


async def test_missing_x_token_skips_twitter(settings, sent):
    settings.secrets.x_bearer_token = None
    with respx.mock(assert_all_called=False) as router:
        mock_sources(router)
        result = await pipeline.run(settings, RunOptions(preview_path=None))
    assert "twitter" in result.stats.skipped
    assert "twitter" not in result.stats.errors
