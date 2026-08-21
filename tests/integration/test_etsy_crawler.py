from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend.crawler.adapters.etsy import EtsyAdapter, EtsyCredentials
from backend.crawler.errors import UpstreamError
from backend.crawler.service import EtsyCrawler
from backend.crawler.transport import JsonResponse
from backend.domain.models import EtsyCrawlRequest, RunStatus
from backend.infrastructure.sqlite_repository import SQLiteRawProductRepository

FIXTURES = Path(__file__).parents[1] / "fixtures" / "etsy"


class SequencedTransport:
    def __init__(self, payloads: list[dict[str, Any] | Exception]) -> None:
        self.payloads = iter(payloads)
        self.calls = 0

    async def get_json(self, url: str, *, params: Any, headers: Any) -> JsonResponse:
        self.calls += 1
        payload = next(self.payloads)
        if isinstance(payload, Exception):
            raise payload
        return JsonResponse(
            status_code=200,
            headers={},
            payload=payload,
            attempts=1,
        )


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


async def test_etsy_vertical_slice_persists_immutable_raw_observations(tmp_path: Path) -> None:
    repository = SQLiteRawProductRepository(tmp_path / "etsy.sqlite3")
    transport = SequencedTransport([fixture("page_1.json"), fixture("page_2.json")])
    crawler = EtsyCrawler(
        EtsyAdapter(transport, EtsyCredentials("key:secret")),
        repository,
    )

    summary = await crawler.crawl(
        EtsyCrawlRequest(keyword="Christmas Ornament", page_size=2, max_pages=5),
        run_id="run-1",
    )

    assert summary.status is RunStatus.SUCCEEDED
    assert summary.records_collected == 3
    assert summary.pages_fetched == 2
    assert summary.requests_count == 2
    assert repository.get_run("run-1")["status"] == "succeeded"
    products = repository.list_products("run-1")
    assert [product["source_product_id"] for product in products] == ["1001", "1002", "1003"]
    assert json.loads(products[0]["raw_payload"])["listing_id"] == 1001

    with (
        sqlite3.connect(repository.database_path) as connection,
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
    ):
        connection.execute(
            "UPDATE raw_product_observations SET title = 'changed' WHERE id = 1"
        )


async def test_duplicate_listing_in_same_run_is_ignored(tmp_path: Path) -> None:
    first = fixture("page_1.json")
    duplicate_page = {
        "count": 3,
        "results": [first["results"][1]],
    }
    repository = SQLiteRawProductRepository(tmp_path / "etsy.sqlite3")
    crawler = EtsyCrawler(
        EtsyAdapter(
            SequencedTransport([first, duplicate_page]),
            EtsyCredentials("key:secret"),
        ),
        repository,
    )

    summary = await crawler.crawl(
        EtsyCrawlRequest(keyword="ornament", page_size=2, max_pages=2),
        run_id="duplicate-run",
    )

    assert summary.records_collected == 2
    assert len(repository.list_products("duplicate-run")) == 2


class FailingAdapter:
    async def fetch_page(self, request: EtsyCrawlRequest, *, offset: int) -> Any:
        raise UpstreamError("fixture upstream failure")


async def test_failed_crawl_is_auditable(tmp_path: Path) -> None:
    repository = SQLiteRawProductRepository(tmp_path / "etsy.sqlite3")
    crawler = EtsyCrawler(FailingAdapter(), repository)

    with pytest.raises(UpstreamError):
        await crawler.crawl(EtsyCrawlRequest(keyword="ornament"), run_id="failed-run")

    run = repository.get_run("failed-run")
    assert run["status"] == "failed"
    assert run["error_type"] == "UpstreamError"
    assert run["error_message"] == "fixture upstream failure"


async def test_failure_after_first_page_preserves_partial_record_count(tmp_path: Path) -> None:
    repository = SQLiteRawProductRepository(tmp_path / "etsy.sqlite3")
    crawler = EtsyCrawler(
        EtsyAdapter(
            SequencedTransport(
                [fixture("page_1.json"), UpstreamError("second page failed")]
            ),
            EtsyCredentials("key:secret"),
        ),
        repository,
    )

    with pytest.raises(UpstreamError, match="second page failed"):
        await crawler.crawl(
            EtsyCrawlRequest(keyword="ornament", page_size=2, max_pages=2),
            run_id="partial-run",
        )

    run = repository.get_run("partial-run")
    assert run["status"] == "failed"
    assert run["records_collected"] == 2
    assert len(repository.list_products("partial-run")) == 2
