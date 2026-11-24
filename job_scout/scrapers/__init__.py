from job_scout.models import Source
from job_scout.scrapers.base import Scraper, SourceSkipped
from job_scout.scrapers.hackernews import HackerNewsScraper
from job_scout.scrapers.hasjob import HasJobScraper
from job_scout.scrapers.linkedin import LinkedInScraper
from job_scout.scrapers.twitter import TwitterScraper
from job_scout.scrapers.wellfound import WellfoundScraper

SCRAPERS: dict[Source, type[Scraper]] = {
    Source.LINKEDIN: LinkedInScraper,
    Source.HACKERNEWS: HackerNewsScraper,
    Source.TWITTER: TwitterScraper,
    Source.WELLFOUND: WellfoundScraper,
    Source.HASJOB: HasJobScraper,
}

__all__ = ["SCRAPERS", "Scraper", "SourceSkipped"]
