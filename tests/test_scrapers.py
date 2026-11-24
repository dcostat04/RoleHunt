from job_scout.models import Source
from job_scout.scrapers import hackernews, hasjob, linkedin, twitter, wellfound

from conftest import HN_ITEM, HN_SEARCH, TWEETS, fixture_text, wellfound_html


def test_linkedin_search_parsing():
    jobs = linkedin.parse_search_results(fixture_text("linkedin_search.html"))
    assert [j.title for j in jobs] == ["Senior Product Manager", "Product Marketing Manager", "Product Manager, Payments"]
    first = jobs[0]
    assert first.company == "Razorpay"
    assert first.location == "Bengaluru, Karnataka, India"
    assert first.external_id == "4012345678"
    assert first.url == "https://www.linkedin.com/jobs/view/4012345678"
    assert first.posted_at is not None


def test_linkedin_detail_parsing():
    html = '<section><div class="show-more-less-html__markup"><p>Own the roadmap.</p><ul><li>5+ yrs</li></ul></div></section>'
    assert linkedin.parse_job_detail(html) == "Own the roadmap.\n5+ yrs"


def test_hn_picks_latest_who_is_hiring_thread():
    assert hackernews.find_hiring_thread(HN_SEARCH) == "45000000"
    assert hackernews.find_hiring_thread({"hits": []}) is None


def test_hn_comment_parsing():
    jobs = hackernews.parse_thread(HN_ITEM)
    assert len(jobs) == 3  # deleted comment dropped
    rp = jobs[0]
    assert rp.company == "Razorpay"
    assert rp.title == "Senior Product Manager"
    assert "Bengaluru, India" in rp.location and "Hybrid" in rp.location
    assert "payments platform" in rp.description
    assert rp.url == "https://news.ycombinator.com/item?id=45000010"


def test_twitter_parsing():
    jobs = twitter.parse_search_response(TWEETS)
    assert len(jobs) == 1
    tw = jobs[0]
    assert tw.url == "https://x.com/cofounder/status/1861000000000000001"
    assert tw.company == "Example Labs"
    assert "https://jobs.example.com/pm" in tw.description
    assert "-is:retweet" in twitter.build_query(["product manager"])


def test_wellfound_parsing():
    jobs = wellfound.parse_listing_page(wellfound_html())
    by_id = {j.external_id: j for j in jobs}
    assert set(by_id) == {"9001", "9002"}
    sr = by_id["9001"]
    assert sr.company == "Razorpay"
    assert sr.location == "Bengaluru"
    assert sr.url == "https://wellfound.com/jobs/9001-senior-product-manager"
    assert "Lead our checkout product." in sr.description
    assert by_id["9002"].location == "Remote, India"


def test_wellfound_without_next_data():
    assert wellfound.parse_listing_page("<html><body>blocked</body></html>") == []


def test_hasjob_feed_parsing():
    jobs = hasjob.parse_feed(fixture_text("hasjob_feed.xml"))
    assert len(jobs) == 2
    pm = jobs[0]
    assert pm.source is Source.HASJOB
    assert pm.company == "Zerodha"
    assert pm.location == "Bangalore"
    assert pm.url == "https://hasjob.co/bangalore/x7k2p"
    assert pm.external_id == "x7k2p"
    # Second entry has no author: company derived from "X at Company (Loc)"
    assert jobs[1].company == "Setu"
    assert jobs[1].location == "Remote"


def test_hn_header_variants():
    plane = hackernews.parse_thread(HN_ITEM)[2]
    assert plane.company == "Plane"
    assert plane.title == "Product Manager, Product Designer"
    assert plane.location.startswith("REMOTE")

    free = hackernews.parse_comment({"id": 1, "author": "a", "text": "We are hiring PMs in Pune!<p>Details..."})
    assert free.title == "We are hiring PMs in Pune!" and free.company == ""

    salary = hackernews.parse_comment(
        {"id": 2, "author": "a", "text": "Foo | $150k-$200k | Head of Product | Remote (US) | Full-time"})
    assert salary.title == "Head of Product"
    assert salary.location == "Remote (US)"
