from __future__ import annotations

from typing import Protocol

from backend.domain.models import CrawlPage, EtsyCrawlRequest, RawProduct


class EtsyPageSource(Protocol):
    async def fetch_page(self, request: EtsyCrawlRequest, *, offset: int) -> CrawlPage: ...


class RawProductRepository(Protocol):
    async def initialize(self) -> None: ...

    async def start_run(self, run_id: str, *, source: str, query: str) -> None: ...

    async def save_products(self, run_id: str, products: tuple[RawProduct, ...]) -> int: ...

    async def mark_succeeded(
        self,
        run_id: str,
        *,
        records_collected: int,
        requests_count: int,
        retry_count: int,
    ) -> None: ...

    async def mark_failed(
        self,
        run_id: str,
        *,
        records_collected: int,
        error_type: str,
        error_message: str,
        requests_count: int,
        retry_count: int,
    ) -> None: ...
