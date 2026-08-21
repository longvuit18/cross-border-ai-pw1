from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.crawler.errors import CrawlTimeoutError, UpstreamError
from backend.crawler.firecrawl_worker import FirecrawlWorker
from backend.crawler.transport import JsonResponse
from backend.domain.firecrawl_models import (
    FirecrawlCrawlRequest,
    FirecrawlPage,
    FirecrawlStatus,
)
from backend.infrastructure.firecrawl_repository import SQLiteFirecrawlRepository

FIXTURES = Path(__file__).parents[1] / "fixtures" / "firecrawl"
JOB_ID = "11111111-1111-4111-8111-111111111111"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def status_from_fixture(name: str) -> FirecrawlStatus:
    payload = fixture(name)
    pages = tuple(
        FirecrawlPage(
            source_url=item["metadata"]["sourceURL"],
            title=item["metadata"].get("title"),
            markdown=item.get("markdown"),
            raw_payload=item,
        )
        for item in payload["data"]
    )
    return FirecrawlStatus(
        provider_status=payload["status"],
        total=payload["total"],
        completed=payload["completed"],
        credits_used=payload["creditsUsed"],
        pages=pages,
        next_url=payload.get("next"),
        attempts=1,
        retry_count=0,
        raw_payload=payload,
    )


class FakeFirecrawlClient:
    def __init__(self, statuses: list[FirecrawlStatus]) -> None:
        self.statuses = iter(statuses)
        self.starts = 0
        self.polls = 0
        self.cancelled: list[str] = []

    async def start_crawl(
        self,
        request: FirecrawlCrawlRequest,
    ) -> tuple[str, JsonResponse]:
        self.starts += 1
        return JOB_ID, JsonResponse(
            status_code=200,
            headers={},
            payload={"success": True, "id": JOB_ID},
            attempts=1,
        )

    async def get_status(self, job_id: str) -> FirecrawlStatus:
        self.polls += 1
        return next(self.statuses)

    async def get_status_url(self, url: str) -> FirecrawlStatus:
        return next(self.statuses)

    async def cancel_crawl(self, job_id: str) -> None:
        self.cancelled.append(job_id)


def request(**overrides: Any) -> FirecrawlCrawlRequest:
    values: dict[str, Any] = {
        "url": "https://example.com",
        "limit": 2,
        "proxy": "basic",
        "credit_budget": 2,
        "poll_interval_seconds": 0.001,
        "max_poll_attempts": 3,
    }
    values.update(overrides)
    return FirecrawlCrawlRequest(**values)


async def no_sleep(seconds: float) -> None:
    return None


async def test_worker_polls_persists_pages_and_deduplicates_partial_results(
    tmp_path: Path,
) -> None:
    repository = SQLiteFirecrawlRepository(tmp_path / "firecrawl.sqlite3")
    client = FakeFirecrawlClient(
        [
            status_from_fixture("status_scraping.json"),
            status_from_fixture("status_completed.json"),
        ]
    )
    worker = FirecrawlWorker(client, repository, sleeper=no_sleep)

    summary = await worker.run(request(), run_id="run-1")

    assert summary.status == "succeeded"
    assert summary.pages_stored == 2
    assert summary.credits_used == 2
    assert summary.requests_count == 3
    assert len(repository.list_pages("run-1")) == 2
    assert repository.get_run("run-1")["provider_job_id"] == JOB_ID


async def test_completed_run_is_reused_from_local_cache(tmp_path: Path) -> None:
    repository = SQLiteFirecrawlRepository(tmp_path / "firecrawl.sqlite3")
    client = FakeFirecrawlClient([status_from_fixture("status_completed.json")])
    worker = FirecrawlWorker(client, repository, sleeper=no_sleep)

    first = await worker.run(request(), run_id="cache-source")
    second = await worker.run(request(), run_id="unused-run")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.run_id == "cache-source"
    assert client.starts == 1


async def test_failed_provider_status_is_auditable(tmp_path: Path) -> None:
    repository = SQLiteFirecrawlRepository(tmp_path / "firecrawl.sqlite3")
    failed = FirecrawlStatus(
        provider_status="failed",
        total=1,
        completed=0,
        credits_used=1,
        pages=(),
        next_url=None,
        attempts=1,
        retry_count=0,
        raw_payload={"status": "failed"},
    )
    worker = FirecrawlWorker(FakeFirecrawlClient([failed]), repository, sleeper=no_sleep)

    with pytest.raises(UpstreamError, match="failed"):
        await worker.run(request(), run_id="failed-run")

    run = repository.get_run("failed-run")
    assert run["status"] == "failed"
    assert run["error_type"] == "UpstreamError"


async def test_timeout_cancels_remote_job(tmp_path: Path) -> None:
    repository = SQLiteFirecrawlRepository(tmp_path / "firecrawl.sqlite3")
    scraping = status_from_fixture("status_scraping.json")
    client = FakeFirecrawlClient([scraping, scraping])
    worker = FirecrawlWorker(client, repository, sleeper=no_sleep)

    with pytest.raises(CrawlTimeoutError):
        await worker.run(
            request(max_poll_attempts=2),
            run_id="timeout-run",
        )

    assert client.cancelled == [JOB_ID]
    assert repository.get_run("timeout-run")["status"] == "failed"

