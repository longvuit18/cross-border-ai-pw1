from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.crawler.errors import ConfigurationError, MalformedResponseError
from backend.crawler.transport import JsonResponse, JsonTransport
from backend.domain.firecrawl_models import (
    FirecrawlBatchScrapeRequest,
    FirecrawlCrawlRequest,
    FirecrawlPage,
    FirecrawlScrapeRequest,
    FirecrawlStatus,
)


@dataclass(frozen=True, slots=True)
class FirecrawlCredentials:
    api_key: str

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ConfigurationError("Firecrawl API key must not be empty")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> FirecrawlCredentials:
        values = os.environ if environ is None else environ
        api_key = values.get("FIRECRAWL_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("Set FIRECRAWL_API_KEY")
        return cls(api_key=api_key)


class FirecrawlClient:
    def __init__(
        self,
        transport: JsonTransport,
        credentials: FirecrawlCredentials,
        *,
        base_url: str = "https://api.firecrawl.dev/v2",
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._base_url = base_url.rstrip("/")
        parsed = urlparse(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError("FIRECRAWL_API_URL must be an absolute URL")
        self._allowed_origin = (parsed.scheme, parsed.netloc)

    @classmethod
    def from_env(
        cls,
        transport: JsonTransport,
        environ: Mapping[str, str] | None = None,
    ) -> FirecrawlClient:
        values = os.environ if environ is None else environ
        return cls(
            transport,
            FirecrawlCredentials.from_env(values),
            base_url=values.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2"),
        )

    async def start_crawl(
        self,
        request: FirecrawlCrawlRequest,
    ) -> tuple[str, JsonResponse]:
        response = await self._transport.request_json(
            "POST",
            f"{self._base_url}/crawl",
            headers=self._headers,
            json_body=request.to_api_payload(),
        )
        job_id = response.payload.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise MalformedResponseError("Firecrawl start response is missing job id")
        return job_id, response

    async def get_status(self, job_id: str) -> FirecrawlStatus:
        response = await self._transport.request_json(
            "GET",
            f"{self._base_url}/crawl/{job_id}",
            headers=self._headers,
        )
        return self._parse_status(response)

    async def get_status_url(self, url: str) -> FirecrawlStatus:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != self._allowed_origin:
            raise MalformedResponseError("Firecrawl next URL changed origin")
        response = await self._transport.request_json(
            "GET",
            url,
            headers=self._headers,
        )
        return self._parse_status(response)

    async def cancel_crawl(self, job_id: str) -> None:
        await self._transport.request_json(
            "DELETE",
            f"{self._base_url}/crawl/{job_id}",
            headers=self._headers,
        )

    async def scrape(
        self,
        request: FirecrawlScrapeRequest,
    ) -> tuple[FirecrawlPage, JsonResponse]:
        response = await self._transport.request_json(
            "POST",
            f"{self._base_url}/scrape",
            headers=self._headers,
            json_body=request.to_api_payload(),
        )
        payload = response.payload
        if payload.get("success") is not True:
            raise MalformedResponseError("Firecrawl scrape response was not successful")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MalformedResponseError("Firecrawl scrape response is missing data")

        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        status_code = metadata.get("statusCode")
        if isinstance(status_code, int) and status_code >= 400:
            raise MalformedResponseError(
                f"Firecrawl target returned HTTP {status_code}"
            )
        markdown_value = data.get("markdown")
        markdown = markdown_value if isinstance(markdown_value, str) else None
        links = data.get("links")
        if not markdown and not (isinstance(links, list) and links):
            raise MalformedResponseError("Firecrawl scrape returned no markdown or links")

        source_value = metadata.get("sourceURL") or metadata.get("url")
        source_url = source_value if isinstance(source_value, str) else request.url
        title_value = metadata.get("title")
        if isinstance(title_value, list):
            title_value = next(
                (value for value in title_value if isinstance(value, str)), None
            )
        title = title_value if isinstance(title_value, str) else None
        return (
            FirecrawlPage(
                source_url=source_url,
                title=title,
                markdown=markdown,
                raw_payload=data,
            ),
            response,
        )

    async def start_batch_scrape(
        self,
        request: FirecrawlBatchScrapeRequest,
    ) -> tuple[str, JsonResponse]:
        response = await self._transport.request_json(
            "POST",
            f"{self._base_url}/batch/scrape",
            headers=self._headers,
            json_body=request.to_api_payload(),
        )
        job_id = response.payload.get("id")
        if response.payload.get("success") is not True or not isinstance(job_id, str) or not job_id:
            raise MalformedResponseError("Firecrawl batch response is missing job id")
        return job_id, response

    async def get_batch_status(self, job_id: str) -> FirecrawlStatus:
        response = await self._transport.request_json(
            "GET",
            f"{self._base_url}/batch/scrape/{job_id}",
            headers=self._headers,
        )
        return self._parse_status(response)

    async def get_batch_status_url(self, url: str) -> FirecrawlStatus:
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != self._allowed_origin:
            raise MalformedResponseError("Firecrawl batch next URL changed origin")
        response = await self._transport.request_json(
            "GET",
            url,
            headers=self._headers,
        )
        return self._parse_status(response)

    async def get_batch_errors(
        self,
        job_id: str,
    ) -> tuple[dict[str, Any], JsonResponse]:
        response = await self._transport.request_json(
            "GET",
            f"{self._base_url}/batch/scrape/{job_id}/errors",
            headers=self._headers,
        )
        return response.payload, response

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {self._credentials.api_key}",
        }

    @staticmethod
    def _parse_status(response: JsonResponse) -> FirecrawlStatus:
        payload = response.payload
        status = payload.get("status")
        if not isinstance(status, str):
            raise MalformedResponseError("Firecrawl status response is missing status")
        pages_payload = payload.get("data", [])
        if not isinstance(pages_payload, list):
            raise MalformedResponseError("Firecrawl status data must be a list")

        pages: list[FirecrawlPage] = []
        for value in pages_payload:
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            source_url = metadata.get("sourceURL") or metadata.get("url")
            if not isinstance(source_url, str) or not source_url:
                continue
            title_value = metadata.get("title")
            title = title_value if isinstance(title_value, str) else None
            markdown_value = value.get("markdown")
            markdown = markdown_value if isinstance(markdown_value, str) else None
            pages.append(
                FirecrawlPage(
                    source_url=source_url,
                    title=title,
                    markdown=markdown,
                    raw_payload=value,
                )
            )

        return FirecrawlStatus(
            provider_status=status,
            total=FirecrawlClient._non_negative_int(payload.get("total")),
            completed=FirecrawlClient._non_negative_int(payload.get("completed")),
            credits_used=FirecrawlClient._non_negative_int(payload.get("creditsUsed")),
            pages=tuple(pages),
            next_url=payload.get("next") if isinstance(payload.get("next"), str) else None,
            attempts=response.attempts,
            retry_count=response.retry_count,
            raw_payload=payload,
        )

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
