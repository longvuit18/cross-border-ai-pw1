from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.crawler.adapters.etsy import EtsyAdapter, EtsyCredentials
from backend.crawler.errors import ConfigurationError, MalformedResponseError
from backend.crawler.transport import JsonResponse
from backend.domain.models import EtsyCrawlRequest, SourceType

FIXTURES = Path(__file__).parents[1] / "fixtures" / "etsy"


class StubTransport:
    def __init__(self, payload: dict[str, Any], *, attempts: int = 1) -> None:
        self.payload = payload
        self.attempts = attempts
        self.calls: list[dict[str, Any]] = []

    async def get_json(self, url: str, *, params: Any, headers: Any) -> JsonResponse:
        self.calls.append({"url": url, "params": dict(params), "headers": dict(headers)})
        return JsonResponse(
            status_code=200,
            headers={
                "x-limit-per-second": "10",
                "x-remaining-this-secon": "9",
                "x-limit-per-day": "1000",
                "x-remaining-today": "999",
            },
            payload=self.payload,
            attempts=self.attempts,
        )


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_credentials_can_be_composed_from_environment() -> None:
    credentials = EtsyCredentials.from_env(
        {"ETSY_KEYSTRING": "key", "ETSY_SHARED_SECRET": "secret"}
    )
    assert credentials.api_key == "key:secret"


def test_credentials_fail_fast_when_missing() -> None:
    with pytest.raises(ConfigurationError, match="ETSY_API_KEY"):
        EtsyCredentials.from_env({})


async def test_fetch_page_builds_official_search_request_and_parses_products() -> None:
    transport = StubTransport(fixture("page_1.json"), attempts=2)
    adapter = EtsyAdapter(transport, EtsyCredentials("key:secret"))

    page = await adapter.fetch_page(
        EtsyCrawlRequest(keyword="Christmas Ornament", page_size=2),
        offset=0,
    )

    assert adapter.source_type is SourceType.MARKETPLACE
    assert adapter.capabilities.rating is False
    assert [item.source_product_id for item in page.items] == ["1001", "1002"]
    assert page.items[0].price == Decimal("19.99")
    assert page.items[0].currency == "USD"
    assert page.items[0].rating is None
    assert page.next_offset == 2
    assert page.total_available == 3
    assert page.request_metadata["retry_count"] == 1
    assert page.request_metadata["remaining_this_second"] == "9"

    call = transport.calls[0]
    assert call["url"].endswith("/v3/application/listings/active")
    assert call["params"] == {
        "keywords": "Christmas Ornament",
        "limit": 2,
        "offset": 0,
        "sort_on": "score",
        "sort_order": "desc",
    }
    assert call["headers"]["x-api-key"] == "key:secret"


async def test_last_page_has_no_next_offset() -> None:
    adapter = EtsyAdapter(StubTransport(fixture("page_2.json")), EtsyCredentials("key:secret"))
    page = await adapter.fetch_page(
        EtsyCrawlRequest(keyword="ornament", page_size=2),
        offset=2,
    )
    assert page.next_offset is None
    assert len(page.items) == 1


async def test_invalid_listing_is_rejected_without_losing_valid_listings() -> None:
    payload = fixture("page_1.json")
    payload["results"].append({"title": "missing id"})
    payload["count"] = 3
    adapter = EtsyAdapter(StubTransport(payload), EtsyCredentials("key:secret"))

    page = await adapter.fetch_page(EtsyCrawlRequest(keyword="ornament"), offset=0)

    assert len(page.items) == 2
    assert page.request_metadata["rejected"] == 1


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"count": 1, "results": {}}, "results"),
        ({"count": "1", "results": []}, "count"),
    ],
)
async def test_malformed_page_fails_clearly(payload: dict[str, Any], message: str) -> None:
    adapter = EtsyAdapter(StubTransport(payload), EtsyCredentials("key:secret"))
    with pytest.raises(MalformedResponseError, match=message):
        await adapter.fetch_page(EtsyCrawlRequest(keyword="ornament"), offset=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"keyword": ""},
        {"keyword": "x", "page_size": 0},
        {"keyword": "x", "page_size": 101},
        {"keyword": "x", "max_pages": 0},
        {"keyword": "x", "sort_on": "popular"},
    ],
)
def test_request_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        EtsyCrawlRequest(**kwargs)

