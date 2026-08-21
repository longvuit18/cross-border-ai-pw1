from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from backend.crawler.errors import MalformedResponseError, RequestRejectedError
from backend.crawler.transport import JsonTransport


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


@dataclass(frozen=True, slots=True)
class PrintwayCatalog:
    total_reported: int
    products: tuple[dict[str, Any], ...]
    requests_count: int


class PrintwayCatalogClient:
    """Read Printway's public catalog using the endpoint used by its storefront."""

    def __init__(
        self,
        transport: JsonTransport,
        *,
        base_url: str = "https://apis.printway.io/v1",
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._transport = transport
        self._endpoint = f"{base_url.rstrip('/')}/product-type/all"
        self._user_agent = user_agent

    async def fetch_catalog(self, *, page_size: int = 100) -> PrintwayCatalog:
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be between 1 and 1000")

        first_page, total, attempts = await self._fetch_page(1, page_size)
        products = list(first_page)
        page_count = math.ceil(total / page_size)
        for page in range(2, page_count + 1):
            values, page_total, page_attempts = await self._fetch_page(page, page_size)
            if page_total != total:
                raise MalformedResponseError("Printway catalog total changed during export")
            products.extend(values)
            attempts += page_attempts

        deduplicated = {self._identity(product): product for product in products}
        if len(deduplicated) != total:
            raise MalformedResponseError(
                f"Printway reported {total} products but export contains "
                f"{len(deduplicated)} unique products"
            )
        return PrintwayCatalog(
            total_reported=total,
            products=tuple(deduplicated.values()),
            requests_count=attempts,
        )

    async def _fetch_page(
        self,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int, int]:
        response = await self._transport.request_json(
            "POST",
            self._endpoint,
            headers=self._headers,
            json_body={
                "categories": "all",
                "locations": [],
                "current": page,
                "pageSize": page_size,
                "searchKey": "",
                "techs": [],
                "printArea": [],
                "sortPrice": "",
            },
        )
        payload = response.payload
        if payload.get("success") is not True:
            raise RequestRejectedError(
                f"Printway catalog request failed: {payload.get('message', 'unknown error')}"
            )
        data = payload.get("data")
        total = payload.get("total")
        if not isinstance(data, list) or not isinstance(total, int) or total < 0:
            raise MalformedResponseError("Printway catalog response has invalid data or total")
        products = [value for value in data if isinstance(value, dict)]
        if len(products) != len(data):
            raise MalformedResponseError("Printway catalog contains a non-object product")
        return products, total, response.attempts

    @property
    def _headers(self) -> dict[str, str]:
        fingerprint_source = "|".join(
            (
                self._user_agent,
                "MacIntel",
                "en-US",
                "1512x982",
                "24",
                "-420",
                "10",
            )
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
        return {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "language": "en",
            "origin": "https://printway.io",
            "referer": "https://printway.io/",
            "user-agent": self._user_agent,
            "x-fp": fingerprint,
            "x-requested-with": "en",
        }

    @staticmethod
    def _identity(product: dict[str, Any]) -> str:
        value = product.get("_id") or product.get("slug")
        if not isinstance(value, str) or not value:
            raise MalformedResponseError("Printway product is missing _id and slug")
        return value
