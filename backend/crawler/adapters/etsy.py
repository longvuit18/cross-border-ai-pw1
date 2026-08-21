from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.crawler.errors import ConfigurationError, MalformedResponseError
from backend.crawler.transport import JsonTransport
from backend.domain.models import (
    CrawlPage,
    EtsyCrawlRequest,
    RawProduct,
    SourceCapabilities,
    SourceType,
)


@dataclass(frozen=True, slots=True)
class EtsyCredentials:
    api_key: str

    def __post_init__(self) -> None:
        if not self.api_key.strip() or ":" not in self.api_key:
            raise ConfigurationError(
                "Etsy API key must use the 'keystring:shared_secret' format"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> EtsyCredentials:
        values = os.environ if environ is None else environ
        combined = values.get("ETSY_API_KEY", "").strip()
        if not combined:
            keystring = values.get("ETSY_KEYSTRING", "").strip()
            shared_secret = values.get("ETSY_SHARED_SECRET", "").strip()
            if keystring and shared_secret:
                combined = f"{keystring}:{shared_secret}"
        if not combined:
            raise ConfigurationError(
                "Set ETSY_API_KEY or both ETSY_KEYSTRING and ETSY_SHARED_SECRET"
            )
        return cls(api_key=combined)


class EtsyAdapter:
    source = "etsy"
    source_type = SourceType.MARKETPLACE
    endpoint = "https://api.etsy.com/v3/application/listings/active"
    maximum_offset = 12_000
    capabilities = SourceCapabilities(
        keyword_search=True,
        pagination=True,
        price=True,
        rating=False,
        reviews=False,
        market_filter=False,
        time_filter=False,
    )

    def __init__(self, transport: JsonTransport, credentials: EtsyCredentials) -> None:
        self._transport = transport
        self._credentials = credentials

    async def fetch_page(self, request: EtsyCrawlRequest, *, offset: int) -> CrawlPage:
        if not 0 <= offset <= self.maximum_offset:
            raise ValueError(f"offset must be between 0 and {self.maximum_offset}")

        response = await self._transport.get_json(
            self.endpoint,
            params={
                "keywords": request.keyword,
                "limit": request.page_size,
                "offset": offset,
                "sort_on": request.sort_on,
                "sort_order": request.sort_order,
            },
            headers={
                "accept": "application/json",
                "x-api-key": self._credentials.api_key,
            },
        )

        payload = response.payload
        results = payload.get("results")
        count = payload.get("count")
        if not isinstance(results, list):
            raise MalformedResponseError("Etsy response 'results' must be a list")
        if not isinstance(count, int) or count < 0:
            raise MalformedResponseError("Etsy response 'count' must be a non-negative integer")

        parsed_items: list[RawProduct] = []
        rejected_items = 0
        for listing in results:
            parsed = self._parse_listing(listing)
            if parsed is None:
                rejected_items += 1
            else:
                parsed_items.append(parsed)

        candidate_offset = offset + len(results)
        truncated_by_offset_limit = candidate_offset > self.maximum_offset
        next_offset = (
            candidate_offset
            if results and candidate_offset < count and not truncated_by_offset_limit
            else None
        )

        return CrawlPage(
            items=tuple(parsed_items),
            next_offset=next_offset,
            total_available=count,
            request_metadata={
                "offset": offset,
                "returned": len(results),
                "accepted": len(parsed_items),
                "rejected": rejected_items,
                "attempts": response.attempts,
                "retry_count": response.retry_count,
                "limit_per_second": response.headers.get("x-limit-per-second"),
                "remaining_this_second": response.headers.get("x-remaining-this-second")
                or response.headers.get("x-remaining-this-secon"),
                "limit_per_day": response.headers.get("x-limit-per-day"),
                "remaining_today": response.headers.get("x-remaining-today"),
                "truncated_by_offset_limit": truncated_by_offset_limit,
            },
        )

    def _parse_listing(self, value: Any) -> RawProduct | None:
        if not isinstance(value, dict):
            return None
        listing_id = value.get("listing_id")
        if not isinstance(listing_id, (str, int)) or isinstance(listing_id, bool):
            return None

        title = value.get("title")
        if not isinstance(title, str):
            title = ""

        source_url = value.get("url") if isinstance(value.get("url"), str) else None
        shop_id_value = value.get("shop_id")
        shop_id = (
            str(shop_id_value)
            if isinstance(shop_id_value, (str, int)) and not isinstance(shop_id_value, bool)
            else None
        )
        price, currency = self._parse_price(value.get("price"))

        return RawProduct(
            source=self.source,
            source_product_id=str(listing_id),
            source_url=source_url,
            title=title,
            price=price,
            currency=currency,
            shop_id=shop_id,
            rating=None,
            reviews=None,
            raw_payload=value,
        )

    @staticmethod
    def _parse_price(value: Any) -> tuple[Decimal | None, str | None]:
        if not isinstance(value, dict):
            return None, None
        amount = value.get("amount")
        divisor = value.get("divisor")
        currency = value.get("currency_code")
        if isinstance(amount, bool) or isinstance(divisor, bool):
            return None, currency if isinstance(currency, str) else None
        try:
            parsed_divisor = Decimal(str(divisor))
            if parsed_divisor == 0:
                return None, currency if isinstance(currency, str) else None
            parsed_price = Decimal(str(amount)) / parsed_divisor
        except (InvalidOperation, TypeError):
            parsed_price = None
        return parsed_price, currency if isinstance(currency, str) else None

