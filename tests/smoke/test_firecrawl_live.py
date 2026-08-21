from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.firecrawl_worker import FirecrawlWorker
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.transport import HttpxJsonTransport
from backend.domain.firecrawl_models import FirecrawlCrawlRequest
from backend.infrastructure.firecrawl_repository import SQLiteFirecrawlRepository

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not (os.getenv("RUN_FIRECRAWL_LIVE") == "1" and os.getenv("FIRECRAWL_API_KEY")),
    reason="Set RUN_FIRECRAWL_LIVE=1 and FIRECRAWL_API_KEY",
)
async def test_live_single_page_crawl(tmp_path: Path) -> None:
    transport = HttpxJsonTransport(rate_limiter=AsyncRateLimiter(1.0))
    try:
        repository = SQLiteFirecrawlRepository(tmp_path / "firecrawl-live.sqlite3")
        worker = FirecrawlWorker(
            FirecrawlClient.from_env(transport),
            repository,
        )
        summary = await worker.run(
            FirecrawlCrawlRequest(
                url="https://example.com/",
                max_discovery_depth=0,
                limit=1,
                proxy="basic",
                credit_budget=1,
                poll_interval_seconds=1.0,
                max_poll_attempts=30,
                cache_ttl_seconds=0,
            ),
            force_refresh=True,
        )
    finally:
        await transport.aclose()

    assert summary.status == "succeeded"
    assert summary.pages_stored == 1
    assert summary.credits_used <= 1

