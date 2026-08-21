from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from backend.crawler.errors import PaginationError
from backend.crawler.ports import EtsyPageSource, RawProductRepository
from backend.domain.models import CrawlSummary, EtsyCrawlRequest, RunStatus


class EtsyCrawler:
    source = "etsy"

    def __init__(self, adapter: EtsyPageSource, repository: RawProductRepository) -> None:
        self._adapter = adapter
        self._repository = repository

    async def crawl(
        self,
        request: EtsyCrawlRequest,
        *,
        run_id: str | None = None,
    ) -> CrawlSummary:
        resolved_run_id = run_id or str(uuid4())
        started_at = datetime.now(UTC)
        pages_fetched = 0
        requests_count = 0
        retry_count = 0
        records_collected = 0
        visited_offsets: set[int] = set()

        await self._repository.initialize()
        await self._repository.start_run(
            resolved_run_id,
            source=self.source,
            query=request.keyword,
        )

        offset: int | None = 0
        try:
            while offset is not None and pages_fetched < request.max_pages:
                if offset in visited_offsets:
                    raise PaginationError(f"Etsy pagination repeated offset {offset}")
                visited_offsets.add(offset)

                page = await self._adapter.fetch_page(request, offset=offset)
                pages_fetched += 1
                requests_count += int(page.request_metadata.get("attempts", 1))
                retry_count += int(page.request_metadata.get("retry_count", 0))
                records_collected += await self._repository.save_products(
                    resolved_run_id,
                    page.items,
                )

                next_offset = page.next_offset
                if next_offset is not None and next_offset <= offset:
                    raise PaginationError(
                        f"Etsy pagination did not advance from offset {offset}"
                    )
                offset = next_offset

            finished_at = datetime.now(UTC)
            await self._repository.mark_succeeded(
                resolved_run_id,
                records_collected=records_collected,
                requests_count=requests_count,
                retry_count=retry_count,
            )
            return CrawlSummary(
                run_id=resolved_run_id,
                source=self.source,
                status=RunStatus.SUCCEEDED,
                records_collected=records_collected,
                pages_fetched=pages_fetched,
                requests_count=requests_count,
                retry_count=retry_count,
                started_at=started_at,
                finished_at=finished_at,
            )
        except Exception as exc:
            await self._repository.mark_failed(
                resolved_run_id,
                records_collected=records_collected,
                error_type=type(exc).__name__,
                error_message=str(exc),
                requests_count=requests_count,
                retry_count=retry_count,
            )
            raise
