from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.crawler.adapters.etsy import EtsyAdapter, EtsyCredentials
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.service import EtsyCrawler
from backend.crawler.transport import HttpxJsonTransport
from backend.domain.models import EtsyCrawlRequest, RunStatus
from backend.infrastructure.sqlite_repository import SQLiteRawProductRepository

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not (
        os.getenv("RUN_ETSY_LIVE") == "1"
        and (
            os.getenv("ETSY_API_KEY")
            or (os.getenv("ETSY_KEYSTRING") and os.getenv("ETSY_SHARED_SECRET"))
        )
    ),
    reason="Set RUN_ETSY_LIVE=1 and configure Etsy credentials",
)
async def test_live_etsy_keyword_search(tmp_path: Path) -> None:
    transport = HttpxJsonTransport(rate_limiter=AsyncRateLimiter(2.0))
    try:
        repository = SQLiteRawProductRepository(tmp_path / "etsy-live.sqlite3")
        crawler = EtsyCrawler(
            EtsyAdapter(transport, EtsyCredentials.from_env()),
            repository,
        )
        summary = await crawler.crawl(
            EtsyCrawlRequest(
                keyword="Christmas Ornament",
                page_size=25,
                max_pages=1,
            )
        )
    finally:
        await transport.aclose()

    assert summary.status is RunStatus.SUCCEEDED
    assert summary.records_collected >= 1
    assert len(repository.list_products(summary.run_id)) == summary.records_collected
