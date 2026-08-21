from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.crawler.adapters.firecrawl import FirecrawlClient, FirecrawlCredentials
from backend.crawler.errors import ConfigurationError, MalformedResponseError
from backend.crawler.transport import JsonResponse
from backend.domain.firecrawl_models import (
    FirecrawlBatchScrapeRequest,
    FirecrawlCrawlRequest,
    FirecrawlScrapeRequest,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "firecrawl"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class StubTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)
        self.calls: list[dict[str, Any]] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        headers: Any,
        json_body: Any = None,
    ) -> JsonResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "headers": dict(headers),
                "json_body": json_body,
            }
        )
        return JsonResponse(
            status_code=200,
            headers={},
            payload=next(self.responses),
            attempts=1,
        )


def test_credentials_are_loaded_without_exposing_key() -> None:
    credentials = FirecrawlCredentials.from_env({"FIRECRAWL_API_KEY": "test-secret"})
    assert credentials.api_key == "test-secret"
    with pytest.raises(ConfigurationError, match="FIRECRAWL_API_KEY"):
        FirecrawlCredentials.from_env({})


def test_request_builds_bounded_v2_payload_and_cache_key() -> None:
    request = FirecrawlCrawlRequest(
        url="https://example.com/docs",
        include_paths=("^/docs/.*$",),
        exclude_paths=("^/docs/private/.*$",),
        max_discovery_depth=2,
        limit=4,
        proxy="auto",
        credit_budget=20,
    )
    payload = request.to_api_payload()
    assert payload["includePaths"] == ["^/docs/.*$"]
    assert payload["excludePaths"] == ["^/docs/private/.*$"]
    assert payload["maxDiscoveryDepth"] == 2
    assert payload["limit"] == 4
    assert payload["scrapeOptions"]["proxy"] == "auto"
    assert payload["scrapeOptions"]["location"] == {
        "country": "US",
        "languages": ["en-US"],
    }
    assert len(request.cache_key) == 64


def test_request_rejects_worst_case_credit_overrun() -> None:
    with pytest.raises(ValueError, match="credit_budget"):
        FirecrawlCrawlRequest(
            url="https://example.com",
            limit=10,
            proxy="auto",
            credit_budget=49,
        )


async def test_client_starts_v2_crawl_and_parses_status() -> None:
    transport = StubTransport([fixture("start.json"), fixture("status_completed.json")])
    client = FirecrawlClient(transport, FirecrawlCredentials("test-secret"))
    request = FirecrawlCrawlRequest(
        url="https://example.com",
        limit=2,
        proxy="basic",
        credit_budget=2,
    )

    job_id, _ = await client.start_crawl(request)
    status = await client.get_status(job_id)

    assert job_id == "11111111-1111-4111-8111-111111111111"
    assert status.provider_status == "completed"
    assert status.completed == 2
    assert status.credits_used == 2
    assert [page.source_url for page in status.pages] == [
        "https://example.com/",
        "https://example.com/about",
    ]
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == "https://api.firecrawl.dev/v2/crawl"
    assert transport.calls[0]["headers"]["authorization"] == "Bearer test-secret"
    assert transport.calls[1]["method"] == "GET"


async def test_next_url_must_remain_on_firecrawl_origin() -> None:
    client = FirecrawlClient(StubTransport([]), FirecrawlCredentials("test-secret"))
    with pytest.raises(MalformedResponseError, match="changed origin"):
        await client.get_status_url("https://attacker.example/crawl/result")


async def test_start_response_requires_job_id() -> None:
    client = FirecrawlClient(
        StubTransport([{"success": True}]),
        FirecrawlCredentials("test-secret"),
    )
    with pytest.raises(MalformedResponseError, match="job id"):
        await client.start_crawl(
            FirecrawlCrawlRequest(
                url="https://example.com",
                limit=1,
                proxy="basic",
                credit_budget=1,
            )
        )


async def test_client_scrapes_one_url_and_preserves_links() -> None:
    transport = StubTransport(
        [
            {
                "success": True,
                "data": {
                    "markdown": "# Etsy results",
                    "links": ["https://www.etsy.com/listing/123/example"],
                    "metadata": {
                        "title": "Christmas Ornament - Etsy",
                        "sourceURL": "https://www.etsy.com/search?q=Christmas+Ornament",
                        "statusCode": 200,
                    },
                },
            }
        ]
    )
    client = FirecrawlClient(transport, FirecrawlCredentials("test-secret"))
    request = FirecrawlScrapeRequest(
        url="https://www.etsy.com/search?q=Christmas+Ornament",
        proxy="basic",
        credit_budget=1,
    )

    page, response = await client.scrape(request)

    assert response.attempts == 1
    assert page.title == "Christmas Ornament - Etsy"
    assert page.raw_payload["links"] == ["https://www.etsy.com/listing/123/example"]
    assert transport.calls[0]["url"] == "https://api.firecrawl.dev/v2/scrape"
    assert transport.calls[0]["json_body"] == {
        "url": "https://www.etsy.com/search?q=Christmas+Ornament",
        "formats": ["markdown", "links"],
        "maxAge": 86_400_000,
        "proxy": "basic",
        "location": {"country": "US", "languages": ["en-US"]},
    }


async def test_scrape_rejects_empty_success_response() -> None:
    client = FirecrawlClient(
        StubTransport([{"success": True, "data": {"metadata": {"statusCode": 200}}}]),
        FirecrawlCredentials("test-secret"),
    )

    with pytest.raises(MalformedResponseError, match="no markdown or links"):
        await client.scrape(
            FirecrawlScrapeRequest(
                url="https://www.etsy.com/search?q=test",
                proxy="basic",
                credit_budget=1,
            )
        )


async def test_client_starts_and_polls_batch_scrape() -> None:
    transport = StubTransport(
        [
            {"success": True, "id": "batch-1", "url": "https://api.firecrawl.dev"},
            fixture("status_completed.json"),
            {"errors": [], "robotsBlocked": []},
        ]
    )
    client = FirecrawlClient(transport, FirecrawlCredentials("test-secret"))
    request = FirecrawlBatchScrapeRequest(
        urls=("https://www.etsy.com/listing/1/one",),
        proxy="basic",
        credit_budget=1,
    )

    job_id, _ = await client.start_batch_scrape(request)
    status = await client.get_batch_status(job_id)
    errors, errors_response = await client.get_batch_errors(job_id)

    assert job_id == "batch-1"
    assert status.provider_status == "completed"
    assert errors == {"errors": [], "robotsBlocked": []}
    assert errors_response.attempts == 1
    assert transport.calls[0]["url"].endswith("/v2/batch/scrape")
    assert transport.calls[0]["json_body"]["maxConcurrency"] == 2
    assert transport.calls[0]["json_body"]["location"] == {
        "country": "US",
        "languages": ["en-US"],
    }
    assert transport.calls[1]["url"].endswith("/v2/batch/scrape/batch-1")
    assert transport.calls[2]["url"].endswith("/v2/batch/scrape/batch-1/errors")
