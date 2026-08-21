from __future__ import annotations

import json
from pathlib import Path

from backend.crawler.firecrawl_worker import FirecrawlWorker
from backend.crawler.transport import JsonResponse
from backend.domain.firecrawl_models import (
    FirecrawlCrawlRequest,
    FirecrawlPage,
    FirecrawlStatus,
)
from backend.infrastructure.json_firecrawl_repository import JsonFirecrawlRepository


class CompletedClient:
    async def start_crawl(
        self, request: FirecrawlCrawlRequest
    ) -> tuple[str, JsonResponse]:
        return "job-1", JsonResponse(200, {}, {"id": "job-1"}, 1)

    async def get_status(self, job_id: str) -> FirecrawlStatus:
        return FirecrawlStatus(
            provider_status="completed",
            total=1,
            completed=1,
            credits_used=1,
            pages=(
                FirecrawlPage(
                    source_url="https://www.etsy.com/search?q=ornament",
                    title="Etsy search",
                    markdown="# Result",
                    raw_payload={"markdown": "# Result", "metadata": {"x": 1}},
                ),
            ),
            next_url=None,
            attempts=1,
            retry_count=0,
            raw_payload={"status": "completed"},
        )

    async def get_status_url(self, url: str) -> FirecrawlStatus:
        raise AssertionError("unexpected pagination")

    async def cancel_crawl(self, job_id: str) -> None:
        raise AssertionError("unexpected cancellation")


async def no_sleep(seconds: float) -> None:
    return None


async def test_worker_writes_complete_page_payload_to_json(tmp_path: Path) -> None:
    output = tmp_path / "etsy.json"
    repository = JsonFirecrawlRepository(output)
    worker = FirecrawlWorker(CompletedClient(), repository, sleeper=no_sleep)
    request = FirecrawlCrawlRequest(
        url="https://www.etsy.com/search?q=ornament",
        max_discovery_depth=0,
        limit=1,
        proxy="basic",
        credit_budget=1,
    )

    summary = await worker.run(request, run_id="run-json")
    document = json.loads(output.read_text(encoding="utf-8"))

    assert summary.pages_stored == 1
    assert document["run"]["status"] == "succeeded"
    assert document["pages"][0]["markdown"] == "# Result"
    assert document["pages"][0]["raw_payload"]["metadata"] == {"x": 1}
