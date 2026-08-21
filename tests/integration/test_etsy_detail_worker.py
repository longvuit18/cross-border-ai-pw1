from __future__ import annotations

import json
from pathlib import Path

from backend.crawler.etsy_detail_worker import EtsyDetailWorker, canonical_listing_urls
from backend.crawler.transport import JsonResponse
from backend.domain.firecrawl_models import FirecrawlPage, FirecrawlStatus


SEARCH_LINKS = [
    "https://www.etsy.com/listing/101/first?ref=search",
    "https://www.etsy.com/listing/101/first?variation1=2",
    "https://www.etsy.com/listing/202/second?ref=search",
    "https://www.etsy.com/listing/303/third?ref=search",
    "https://example.com/not-etsy",
]


class FakeBatchClient:
    def __init__(self) -> None:
        self.selected: list[str] = []

    async def start_batch_scrape(self, request):
        self.selected = list(request.urls)
        return "batch-test", JsonResponse(200, {}, {"id": "batch-test"}, 1)

    async def get_batch_status(self, job_id: str) -> FirecrawlStatus:
        pages = tuple(
            FirecrawlPage(
                source_url=url,
                title=f"Listing {index}",
                markdown=f"# Listing {index}",
                raw_payload={
                    "markdown": f"# Listing {index}",
                    "metadata": {"sourceURL": url, "statusCode": 200},
                },
            )
            for index, url in enumerate(self.selected, start=1)
        )
        return FirecrawlStatus(
            provider_status="completed",
            total=len(pages),
            completed=len(pages),
            credits_used=len(pages),
            pages=pages,
            next_url=None,
            attempts=1,
            retry_count=0,
            raw_payload={"status": "completed"},
        )

    async def get_batch_status_url(self, url: str) -> FirecrawlStatus:
        raise AssertionError("unexpected pagination")

    async def get_batch_errors(self, job_id: str):
        return (
            {"errors": [], "robotsBlocked": []},
            JsonResponse(200, {}, {"errors": [], "robotsBlocked": []}, 1),
        )


async def no_sleep(seconds: float) -> None:
    return None


def write_search_document(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "firecrawl",
                "run": {"status": "succeeded", "pages_stored": 1},
                "pages": [
                    {
                        "source_url": "https://www.etsy.com/search?q=ornament",
                        "markdown": "# Search",
                        "raw_payload": {"links": SEARCH_LINKS},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_canonical_listing_urls_deduplicate_variations_by_listing_id() -> None:
    assert canonical_listing_urls(SEARCH_LINKS) == [
        "https://www.etsy.com/listing/101/first",
        "https://www.etsy.com/listing/202/second",
        "https://www.etsy.com/listing/303/third",
    ]


async def test_detail_worker_resumes_with_next_unstored_listing(tmp_path: Path) -> None:
    output = tmp_path / "etsy.json"
    write_search_document(output)

    first_client = FakeBatchClient()
    first = await EtsyDetailWorker(
        first_client, output, sleeper=no_sleep
    ).run(max_listings=2, proxy="basic", credit_budget=2)
    second_client = FakeBatchClient()
    second = await EtsyDetailWorker(
        second_client, output, sleeper=no_sleep
    ).run(max_listings=2, proxy="basic", credit_budget=2)
    document = json.loads(output.read_text(encoding="utf-8"))

    assert first.listing_pages_stored == 2
    assert first_client.selected == [
        "https://www.etsy.com/listing/101/first",
        "https://www.etsy.com/listing/202/second",
    ]
    assert second.listing_pages_stored == 1
    assert second_client.selected == ["https://www.etsy.com/listing/303/third"]
    assert len(document["detail_runs"]) == 2
    assert document["detail_runs"][0]["location"] == {
        "country": "US",
        "languages": ["en-US"],
    }
    assert len([page for page in document["pages"] if page.get("page_type") == "listing_detail"]) == 3
