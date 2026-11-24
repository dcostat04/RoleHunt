from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_scout.config import Secrets, Settings

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text()


HN_SEARCH = {
    "hits": [
        {"objectID": "45000001", "title": "Ask HN: Who wants to be hired? (November 2026)", "created_at_i": 1793577600},
        {"objectID": "45000000", "title": "Ask HN: Who is hiring? (November 2026)", "created_at_i": 1793577600},
        {"objectID": "44000000", "title": "Ask HN: Who is hiring? (October 2026)", "created_at_i": 1790899200},
    ]
}

HN_ITEM = {
    "id": 45000000,
    "children": [
        {
            "id": 45000010,
            "author": "founder1",
            "created_at": "2026-11-02T15:04:05.000Z",
            "text": "Razorpay | Senior Product Manager | Bengaluru, India | Hybrid | Full-time"
                    "<p>We are hiring a Senior PM for our payments platform. "
                    "<a href=\"https://razorpay.com/jobs\">Apply</a>",
        },
        {
            "id": 45000011,
            "author": "founder2",
            "created_at": "2026-11-02T16:00:00.000Z",
            "text": "Acme Robotics | Senior Firmware Engineer | Onsite, Boston, MA<p>C, RTOS.",
        },
        {"id": 45000012, "author": None, "text": None},
        {
            "id": 45000013,
            "author": "founder3",
            "created_at": "2026-11-03T08:00:00.000Z",
            "text": "Plane | Product Manager, Product Designer | REMOTE (India timezones)"
                    "<p>Open-source project management tool.",
        },
    ],
}

TWEETS = {
    "data": [
        {
            "id": "1861000000000000001",
            "author_id": "u1",
            "created_at": "2026-11-24T01:00:00.000Z",
            "text": "We're hiring a Product Manager for our AI team in Bengaluru! DM me.",
            "entities": {"urls": [{"expanded_url": "https://jobs.example.com/pm"}]},
        }
    ],
    "includes": {"users": [{"id": "u1", "username": "cofounder", "name": "Example Labs"}]},
}

WELLFOUND_NEXT_DATA = {
    "props": {
        "pageProps": {
            "apolloState": {
                "data": {
                    "StartupResult:111": {
                        "__typename": "StartupResult",
                        "id": "111",
                        "name": "Razorpay",
                        "highConcept": "Payments for India",
                        "highlightedJobListings": [{"__ref": "JobListingSearchResult:9001"}],
                    },
                    "JobListingSearchResult:9001": {
                        "__typename": "JobListingSearchResult",
                        "id": "9001",
                        "title": "Senior Product Manager",
                        "slug": "senior-product-manager",
                        "locationNames": {"type": "json", "json": ["Bengaluru"]},
                        "remote": False,
                        "liveStartAt": 1793900000,
                        "compensation": "₹40L – ₹60L",
                        "description": "<p>Lead our checkout product.</p>",
                    },
                    "StartupResult:222": {
                        "__typename": "StartupResult",
                        "id": "222",
                        "name": "Nimbus",
                        "highlightedJobListings": [{"__ref": "JobListingSearchResult:9002"}],
                    },
                    "JobListingSearchResult:9002": {
                        "__typename": "JobListingSearchResult",
                        "id": "9002",
                        "title": "Product Manager",
                        "slug": "product-manager",
                        "locationNames": ["India"],
                        "remote": True,
                    },
                }
            }
        }
    }
}


def wellfound_html() -> str:
    return (
        "<html><head></head><body><div id='__next'></div>"
        f"<script id='__NEXT_DATA__' type='application/json'>{json.dumps(WELLFOUND_NEXT_DATA)}</script>"
        "</body></html>"
    )


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings.load(Path(__file__).parent.parent / "config.yaml")
    s.dedup.history_file = tmp_path / "seen.json"
    s.search.queries = ["product manager"]
    s.search.linkedin_locations = ["India"]
    s.search.linkedin_pages = 1
    s.search.wellfound_locations = ["india"]
    s.secrets = Secrets(
        gemini_api_key="test-key",
        x_bearer_token="test-token",
        smtp_user="bot@example.com",
        smtp_password="pw",
        email_from="bot@example.com",
        email_to=["me@example.com"],
    )
    return s
