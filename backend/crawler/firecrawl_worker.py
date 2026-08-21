from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import uuid4

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.errors import (
    CrawlerError,
    CrawlTimeoutError,
    CreditBudgetExceeded,
    UpstreamError,
)
from backend.domain.firecrawl_models import (
    FirecrawlCrawlRequest,
    FirecrawlPage,
    FirecrawlSummary,
)


class FirecrawlRepository(Protocol):
    async def initialize(self) -> None: ...

    async def find_cached_summary(
        self, cache_key: str, *, ttl_seconds: int
    ) -> FirecrawlSummary | None: ...

    async def start_run(
        self, run_id: str, *, cache_key: str, start_url: str
    ) -> None: ...

    async def attach_provider_job(
        self,
        run_id: str,
        provider_job_id: str,
        *,
        requests_count: int,
        retry_count: int,
    ) -> None: ...

    async def save_pages(self, run_id: str, pages: tuple[FirecrawlPage, ...]) -> int: ...

    async def update_progress(self, run_id: str, **progress: object) -> None: ...

    async def mark_succeeded(self, run_id: str) -> FirecrawlSummary: ...

    async def mark_failed(
        self, run_id: str, *, error_type: str, error_message: str
    ) -> None: ...


class FirecrawlWorker:
    def __init__(
        self,
        client: FirecrawlClient,
        repository: FirecrawlRepository,
        *,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._repository = repository
        self._sleeper = sleeper

    async def run(
        self,
        request: FirecrawlCrawlRequest,
        *,
        run_id: str | None = None,
        force_refresh: bool = False,
    ) -> FirecrawlSummary:
        await self._repository.initialize()
        if not force_refresh:
            cached = await self._repository.find_cached_summary(
                request.cache_key,
                ttl_seconds=request.cache_ttl_seconds,
            )
            if cached is not None:
                return cached

        resolved_run_id = run_id or str(uuid4())
        await self._repository.start_run(
            resolved_run_id,
            cache_key=request.cache_key,
            start_url=request.url,
        )

        provider_job_id = ""
        requests_count = 0
        retry_count = 0
        pages_stored = 0
        try:
            provider_job_id, start_response = await self._client.start_crawl(request)
            requests_count += start_response.attempts
            retry_count += start_response.retry_count
            await self._repository.attach_provider_job(
                resolved_run_id,
                provider_job_id,
                requests_count=requests_count,
                retry_count=retry_count,
            )

            for poll_index in range(request.max_poll_attempts):
                status = await self._client.get_status(provider_job_id)
                requests_count += status.attempts
                retry_count += status.retry_count
                pages_stored += await self._repository.save_pages(
                    resolved_run_id,
                    status.pages,
                )
                await self._repository.update_progress(
                    resolved_run_id,
                    provider_status=status.provider_status,
                    total=status.total,
                    completed=status.completed,
                    credits_used=status.credits_used,
                    pages_stored=pages_stored,
                    requests_count=requests_count,
                    retry_count=retry_count,
                )

                if status.credits_used > request.credit_budget:
                    await self._best_effort_cancel(provider_job_id)
                    raise CreditBudgetExceeded(
                        f"Firecrawl used {status.credits_used} credits; budget is "
                        f"{request.credit_budget}"
                    )
                if status.provider_status == "completed":
                    await self._collect_remaining_pages(
                        resolved_run_id,
                        status.next_url,
                        request=request,
                        counters={
                            "requests_count": requests_count,
                            "retry_count": retry_count,
                            "pages_stored": pages_stored,
                        },
                    )
                    return await self._repository.mark_succeeded(resolved_run_id)
                if status.provider_status in {"failed", "cancelled"}:
                    raise UpstreamError(
                        f"Firecrawl crawl ended with status {status.provider_status}"
                    )
                if poll_index + 1 < request.max_poll_attempts:
                    await self._sleeper(request.poll_interval_seconds)

            await self._best_effort_cancel(provider_job_id)
            raise CrawlTimeoutError("Firecrawl crawl did not finish before poll limit")
        except Exception as exc:
            await self._repository.mark_failed(
                resolved_run_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            raise

    async def _collect_remaining_pages(
        self,
        run_id: str,
        next_url: str | None,
        *,
        request: FirecrawlCrawlRequest,
        counters: dict[str, int],
    ) -> None:
        visited: set[str] = set()
        while next_url is not None:
            if next_url in visited:
                raise UpstreamError("Firecrawl result pagination repeated next URL")
            visited.add(next_url)
            status = await self._client.get_status_url(next_url)
            counters["requests_count"] += status.attempts
            counters["retry_count"] += status.retry_count
            counters["pages_stored"] += await self._repository.save_pages(run_id, status.pages)
            if status.credits_used > request.credit_budget:
                raise CreditBudgetExceeded(
                    f"Firecrawl used {status.credits_used} credits; budget is "
                    f"{request.credit_budget}"
                )
            await self._repository.update_progress(
                run_id,
                provider_status=status.provider_status,
                total=status.total,
                completed=status.completed,
                credits_used=status.credits_used,
                pages_stored=counters["pages_stored"],
                requests_count=counters["requests_count"],
                retry_count=counters["retry_count"],
            )
            next_url = status.next_url

    async def _best_effort_cancel(self, provider_job_id: str) -> None:
        if not provider_job_id:
            return
        try:
            await self._client.cancel_crawl(provider_job_id)
        except (CrawlerError, ValueError):
            return
