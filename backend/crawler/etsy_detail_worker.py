from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.errors import (
    CrawlTimeoutError,
    CreditBudgetExceeded,
    MalformedResponseError,
    UpstreamError,
)
from backend.domain.firecrawl_models import (
    FirecrawlBatchScrapeRequest,
    FirecrawlPage,
    FirecrawlStatus,
    firecrawl_location_payload,
)

LISTING_PATH = re.compile(r"^/listing/(?P<id>\d+)(?:/(?P<slug>[^/?#]+))?")


@dataclass(frozen=True, slots=True)
class EtsyDetailSummary:
    status: str
    output: str
    listing_links_discovered: int
    listings_selected: int
    listing_pages_stored: int
    listing_errors: int
    credits_used: int
    requests_count: int
    retry_count: int
    batch_job_id: str


def canonical_listing_urls(links: list[Any]) -> list[str]:
    by_id: dict[str, str] = {}
    for value in links:
        if not isinstance(value, str):
            continue
        parsed = urlparse(value)
        if parsed.hostname not in {"etsy.com", "www.etsy.com"}:
            continue
        match = LISTING_PATH.match(parsed.path)
        if match is None:
            continue
        listing_id = match.group("id")
        slug = match.group("slug") or "listing"
        by_id.setdefault(
            listing_id,
            f"https://www.etsy.com/listing/{listing_id}/{slug}",
        )
    return list(by_id.values())


class EtsyDetailWorker:
    def __init__(
        self,
        client: FirecrawlClient,
        output_path: str | Path,
        *,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._output_path = Path(output_path)
        self._sleeper = sleeper

    async def run(
        self,
        *,
        max_listings: int = 10,
        max_concurrency: int = 2,
        proxy: str = "auto",
        max_age_ms: int = 86_400_000,
        credit_budget: int = 50,
        poll_interval_seconds: float = 2.0,
        max_poll_attempts: int = 150,
    ) -> EtsyDetailSummary:
        if not 1 <= max_listings <= 100:
            raise ValueError("max_listings must be between 1 and 100")
        if poll_interval_seconds <= 0 or max_poll_attempts < 1:
            raise ValueError("poll settings must be positive")

        document = self._load_document()
        discovered = self._discover_listing_urls(document)
        existing_ids = self._stored_listing_ids(document)
        selected = [
            url
            for url in discovered
            if self._listing_id(url) not in existing_ids
        ][:max_listings]
        if not selected:
            return EtsyDetailSummary(
                status="nothing_pending",
                output=str(self._output_path),
                listing_links_discovered=len(discovered),
                listings_selected=0,
                listing_pages_stored=0,
                listing_errors=0,
                credits_used=0,
                requests_count=0,
                retry_count=0,
                batch_job_id="",
            )

        request = FirecrawlBatchScrapeRequest(
            urls=tuple(selected),
            max_age_ms=max_age_ms,
            proxy=proxy,
            max_concurrency=max_concurrency,
            credit_budget=credit_budget,
        )
        detail_run = {
            "batch_job_id": None,
            "status": "starting",
            "proxy": proxy,
            "location": firecrawl_location_payload(),
            "credit_budget": credit_budget,
            "maximum_planned_credits": request.maximum_planned_credits,
            "listing_links_discovered": len(discovered),
            "selected_urls": selected,
            "listing_pages_stored": 0,
            "credits_used": 0,
            "requests_count": 0,
            "retry_count": 0,
            "provider_errors": {},
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
        }
        document.setdefault("detail_runs", []).append(detail_run)
        document.setdefault("errors", [])
        self._flush(document)

        batch_job_id = ""
        requests_count = 0
        retry_count = 0
        credits_used = 0
        stored_this_run = 0
        try:
            batch_job_id, start_response = await self._client.start_batch_scrape(request)
            requests_count += start_response.attempts
            retry_count += start_response.retry_count
            detail_run.update(
                {
                    "batch_job_id": batch_job_id,
                    "status": "scraping",
                    "requests_count": requests_count,
                    "retry_count": retry_count,
                }
            )
            self._flush(document)

            final_status: FirecrawlStatus | None = None
            for poll_index in range(max_poll_attempts):
                status = await self._client.get_batch_status(batch_job_id)
                final_status = status
                requests_count += status.attempts
                retry_count += status.retry_count
                credits_used = max(credits_used, status.credits_used)
                stored_this_run += self._save_pages(document, status.pages)
                detail_run.update(
                    {
                        "provider_status": status.provider_status,
                        "listing_pages_stored": stored_this_run,
                        "credits_used": credits_used,
                        "requests_count": requests_count,
                        "retry_count": retry_count,
                    }
                )
                self._flush(document)
                if credits_used > credit_budget:
                    raise CreditBudgetExceeded(
                        f"Firecrawl used {credits_used} credits; budget is {credit_budget}"
                    )
                if status.provider_status == "completed":
                    break
                if status.provider_status in {"failed", "cancelled"}:
                    raise UpstreamError(
                        f"Firecrawl batch ended with status {status.provider_status}"
                    )
                if poll_index + 1 < max_poll_attempts:
                    await self._sleeper(poll_interval_seconds)
            else:
                raise CrawlTimeoutError("Firecrawl batch did not finish before poll limit")

            if final_status is not None and final_status.next_url:
                added, page_requests, page_retries, page_credits = (
                    await self._collect_remaining_pages(document, final_status.next_url)
                )
                stored_this_run += added
                requests_count += page_requests
                retry_count += page_retries
                credits_used = max(credits_used, page_credits)

            provider_errors, errors_response = await self._client.get_batch_errors(
                batch_job_id
            )
            requests_count += errors_response.attempts
            retry_count += errors_response.retry_count
            detail_run["provider_errors"] = provider_errors
            errors_before = len(document["errors"])
            self._store_provider_errors(document, provider_errors)
            selected_ids = {self._listing_id(url) for url in selected}
            completed_ids = self._stored_listing_ids(document) & selected_ids
            missing_ids = sorted(selected_ids - completed_ids)
            reported_error_ids = {
                self._listing_id(value.get("url", ""))
                for value in document["errors"][errors_before:]
                if isinstance(value, dict)
            }
            for listing_id in missing_ids:
                if listing_id in reported_error_ids:
                    continue
                document["errors"].append(
                    {
                        "listing_id": listing_id,
                        "error": "Firecrawl returned no page for selected listing",
                        "observed_at": datetime.now(UTC).isoformat(),
                    }
                )
            error_count = len(document["errors"]) - errors_before
            terminal_status = "succeeded" if error_count == 0 else "partial"
            detail_run.update(
                {
                    "status": terminal_status,
                    "listing_pages_stored": stored_this_run,
                    "listing_errors": error_count,
                    "credits_used": credits_used,
                    "requests_count": requests_count,
                    "retry_count": retry_count,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            )
            self._flush(document)
            return EtsyDetailSummary(
                status=terminal_status,
                output=str(self._output_path),
                listing_links_discovered=len(discovered),
                listings_selected=len(selected),
                listing_pages_stored=stored_this_run,
                listing_errors=error_count,
                credits_used=credits_used,
                requests_count=requests_count,
                retry_count=retry_count,
                batch_job_id=batch_job_id,
            )
        except Exception as exc:
            detail_run.update(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "listing_pages_stored": stored_this_run,
                    "credits_used": credits_used,
                    "requests_count": requests_count,
                    "retry_count": retry_count,
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            )
            self._flush(document)
            raise

    def _load_document(self) -> dict[str, Any]:
        try:
            document = json.loads(self._output_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("run the Etsy keyword scrape before crawling details") from exc
        except json.JSONDecodeError as exc:
            raise MalformedResponseError("Etsy JSON file is invalid") from exc
        if not isinstance(document, dict) or not isinstance(document.get("pages"), list):
            raise MalformedResponseError("Etsy JSON file is missing pages")
        return document

    def _discover_listing_urls(self, document: dict[str, Any]) -> list[str]:
        for page in document["pages"]:
            if not isinstance(page, dict):
                continue
            raw = page.get("raw_payload")
            links = raw.get("links") if isinstance(raw, dict) else None
            if isinstance(links, list):
                urls = canonical_listing_urls(links)
                if urls:
                    return urls
        raise MalformedResponseError("search page contains no Etsy listing links")

    def _stored_listing_ids(self, document: dict[str, Any]) -> set[str]:
        stored: set[str] = set()
        for page in document["pages"]:
            if not isinstance(page, dict):
                continue
            source_url = page.get("source_url")
            if not isinstance(source_url, str):
                continue
            listing_id = self._listing_id(source_url)
            if listing_id:
                stored.add(listing_id)
        return stored

    def _save_pages(
        self,
        document: dict[str, Any],
        pages: tuple[FirecrawlPage, ...],
    ) -> None:
        existing_ids = self._stored_listing_ids(document)
        inserted = 0
        for page in pages:
            listing_id = self._listing_id(page.source_url)
            if not listing_id or listing_id in existing_ids:
                continue
            document["pages"].append(
                {
                    "page_type": "listing_detail",
                    "listing_id": listing_id,
                    "source_url": page.source_url,
                    "title": page.title,
                    "markdown": page.markdown,
                    "raw_payload": page.raw_payload,
                    "observed_at": page.observed_at.isoformat(),
                }
            )
            existing_ids.add(listing_id)
            inserted += 1
        return inserted

    async def _collect_remaining_pages(
        self,
        document: dict[str, Any],
        next_url: str,
    ) -> tuple[int, int, int, int]:
        inserted = requests = retries = credits = 0
        visited: set[str] = set()
        while next_url:
            if next_url in visited:
                raise UpstreamError("Firecrawl batch pagination repeated next URL")
            visited.add(next_url)
            status = await self._client.get_batch_status_url(next_url)
            requests += status.attempts
            retries += status.retry_count
            credits = max(credits, status.credits_used)
            inserted += self._save_pages(document, status.pages)
            self._flush(document)
            next_url = status.next_url or ""
        return inserted, requests, retries, credits

    def _store_provider_errors(
        self,
        document: dict[str, Any],
        payload: dict[str, Any],
    ) -> int:
        values = payload.get("errors")
        values = values if isinstance(values, list) else []
        robots = payload.get("robotsBlocked")
        robots = robots if isinstance(robots, list) else []
        for value in values:
            if isinstance(value, dict):
                document["errors"].append(value)
        for url in robots:
            document["errors"].append({"url": url, "error": "robotsBlocked"})

    @staticmethod
    def _listing_id(url: str) -> str:
        parsed = urlparse(url)
        match = LISTING_PATH.match(parsed.path)
        return match.group("id") if match else ""

    def _flush(self, document: dict[str, Any]) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._output_path.with_suffix(self._output_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self._output_path)
