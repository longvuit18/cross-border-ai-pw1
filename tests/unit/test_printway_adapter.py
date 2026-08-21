from __future__ import annotations

from typing import Any

import pytest

from backend.crawler.adapters.printway import PrintwayCatalogClient
from backend.crawler.errors import MalformedResponseError
from backend.crawler.transport import JsonResponse


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
                "headers": dict(headers),
                "json_body": json_body,
            }
        )
        return JsonResponse(200, {}, next(self.responses), 1)


async def test_catalog_fetches_every_page_and_preserves_raw_product_data() -> None:
    transport = StubTransport(
        [
            {
                "success": True,
                "total": 3,
                "data": [
                    {"_id": "1", "slug": "one", "displayPrice": {"VN": 2.5}},
                    {"_id": "2", "slug": "two", "option": [{"label": "Size"}]},
                ],
            },
            {
                "success": True,
                "total": 3,
                "data": [{"_id": "3", "slug": "three", "mockupUrls": "image"}],
            },
        ]
    )

    catalog = await PrintwayCatalogClient(transport).fetch_catalog(page_size=2)

    assert catalog.total_reported == 3
    assert len(catalog.products) == 3
    assert catalog.products[0]["displayPrice"] == {"VN": 2.5}
    assert [call["json_body"]["current"] for call in transport.calls] == [1, 2]
    assert len(transport.calls[0]["headers"]["x-fp"]) == 64
    assert transport.calls[0]["headers"]["origin"] == "https://printway.io"


async def test_catalog_rejects_changed_total() -> None:
    transport = StubTransport(
        [
            {"success": True, "total": 2, "data": [{"_id": "1"}]},
            {"success": True, "total": 3, "data": [{"_id": "2"}]},
        ]
    )

    with pytest.raises(MalformedResponseError, match="total changed"):
        await PrintwayCatalogClient(transport).fetch_catalog(page_size=1)
