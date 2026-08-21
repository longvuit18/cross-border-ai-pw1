from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class SourceType(StrEnum):
    MARKETPLACE = "marketplace"
    DEMAND_SIGNAL = "demand_signal"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    keyword_search: bool
    pagination: bool
    price: bool
    rating: bool
    reviews: bool
    market_filter: bool
    time_filter: bool


@dataclass(frozen=True, slots=True)
class RawProduct:
    source: str
    source_product_id: str
    source_url: str | None
    title: str
    price: Decimal | None
    currency: str | None
    shop_id: str | None
    rating: Decimal | None
    reviews: int | None
    raw_payload: dict[str, Any]
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CrawlPage:
    items: tuple[RawProduct, ...]
    next_offset: int | None
    total_available: int
    request_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EtsyCrawlRequest:
    keyword: str
    page_size: int = 100
    max_pages: int = 2
    sort_on: str = "score"
    sort_order: str = "desc"

    def __post_init__(self) -> None:
        keyword = self.keyword.strip()
        if not keyword:
            raise ValueError("keyword must not be empty")
        if not 1 <= self.page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if self.max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if self.sort_on not in {"created", "price", "updated", "score"}:
            raise ValueError("unsupported sort_on value")
        if self.sort_order not in {"asc", "ascending", "desc", "descending", "up", "down"}:
            raise ValueError("unsupported sort_order value")
        object.__setattr__(self, "keyword", keyword)


@dataclass(frozen=True, slots=True)
class CrawlSummary:
    run_id: str
    source: str
    status: RunStatus
    records_collected: int
    pages_fetched: int
    requests_count: int
    retry_count: int
    started_at: datetime
    finished_at: datetime

