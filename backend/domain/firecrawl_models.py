from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

FIRECRAWL_LOCATION_COUNTRY = "US"
FIRECRAWL_LOCATION_LANGUAGES = ("en-US",)


def firecrawl_location_payload() -> dict[str, Any]:
    return {
        "country": FIRECRAWL_LOCATION_COUNTRY,
        "languages": list(FIRECRAWL_LOCATION_LANGUAGES),
    }


@dataclass(frozen=True, slots=True)
class FirecrawlCrawlRequest:
    url: str
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    max_discovery_depth: int = 1
    limit: int = 10
    formats: tuple[str, ...] = ("markdown",)
    max_age_ms: int = 86_400_000
    proxy: str = "auto"
    credit_budget: int = 50
    poll_interval_seconds: float = 2.0
    max_poll_attempts: int = 150
    cache_ttl_seconds: int = 86_400

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        if not 1 <= self.limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        if not 0 <= self.max_discovery_depth <= 20:
            raise ValueError("max_discovery_depth must be between 0 and 20")
        if self.proxy not in {"basic", "enhanced", "auto"}:
            raise ValueError("proxy must be basic, enhanced, or auto")
        if not self.formats:
            raise ValueError("at least one output format is required")
        if self.max_age_ms < 0:
            raise ValueError("max_age_ms must be non-negative")
        if self.credit_budget < 1:
            raise ValueError("credit_budget must be positive")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be positive")
        if self.cache_ttl_seconds < 0:
            raise ValueError("cache_ttl_seconds must be non-negative")
        if self.maximum_planned_credits > self.credit_budget:
            raise ValueError(
                "crawl can exceed credit_budget; lower limit or use a larger budget"
            )

    @property
    def maximum_credit_per_page(self) -> int:
        return 1 if self.proxy == "basic" else 5

    @property
    def maximum_planned_credits(self) -> int:
        return self.limit * self.maximum_credit_per_page

    @property
    def cache_key(self) -> str:
        payload = {
            "url": self.url,
            "include_paths": self.include_paths,
            "exclude_paths": self.exclude_paths,
            "max_discovery_depth": self.max_discovery_depth,
            "limit": self.limit,
            "formats": self.formats,
            "max_age_ms": self.max_age_ms,
            "proxy": self.proxy,
            "location": firecrawl_location_payload(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_api_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": self.url,
            "maxDiscoveryDepth": self.max_discovery_depth,
            "limit": self.limit,
            "scrapeOptions": {
                "formats": list(self.formats),
                "maxAge": self.max_age_ms,
                "proxy": self.proxy,
                "location": firecrawl_location_payload(),
            },
        }
        if self.include_paths:
            payload["includePaths"] = list(self.include_paths)
        if self.exclude_paths:
            payload["excludePaths"] = list(self.exclude_paths)
        return payload


@dataclass(frozen=True, slots=True)
class FirecrawlScrapeRequest:
    url: str
    formats: tuple[str, ...] = ("markdown", "links")
    max_age_ms: int = 86_400_000
    proxy: str = "auto"
    credit_budget: int = 5

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        if not self.formats:
            raise ValueError("at least one output format is required")
        if self.max_age_ms < 0:
            raise ValueError("max_age_ms must be non-negative")
        if self.proxy not in {"basic", "enhanced", "auto"}:
            raise ValueError("proxy must be basic, enhanced, or auto")
        if self.credit_budget < self.maximum_planned_credits:
            raise ValueError(
                "scrape can exceed credit_budget; use basic proxy or a larger budget"
            )

    @property
    def maximum_planned_credits(self) -> int:
        return 1 if self.proxy == "basic" else 5

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "formats": list(self.formats),
            "maxAge": self.max_age_ms,
            "proxy": self.proxy,
            "location": firecrawl_location_payload(),
        }


@dataclass(frozen=True, slots=True)
class FirecrawlBatchScrapeRequest:
    urls: tuple[str, ...]
    formats: tuple[str, ...] = ("markdown", "links")
    max_age_ms: int = 86_400_000
    proxy: str = "auto"
    max_concurrency: int = 2
    credit_budget: int = 50

    def __post_init__(self) -> None:
        if not self.urls:
            raise ValueError("at least one batch URL is required")
        for url in self.urls:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("every batch URL must be absolute HTTP or HTTPS")
        if len(set(self.urls)) != len(self.urls):
            raise ValueError("batch URLs must be unique")
        if not self.formats:
            raise ValueError("at least one output format is required")
        if self.max_age_ms < 0:
            raise ValueError("max_age_ms must be non-negative")
        if self.proxy not in {"basic", "enhanced", "auto"}:
            raise ValueError("proxy must be basic, enhanced, or auto")
        if not 1 <= self.max_concurrency <= 20:
            raise ValueError("max_concurrency must be between 1 and 20")
        if self.credit_budget < self.maximum_planned_credits:
            raise ValueError(
                "batch can exceed credit_budget; select fewer URLs or a larger budget"
            )

    @property
    def maximum_credit_per_page(self) -> int:
        return 1 if self.proxy == "basic" else 5

    @property
    def maximum_planned_credits(self) -> int:
        return len(self.urls) * self.maximum_credit_per_page

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "urls": list(self.urls),
            "formats": list(self.formats),
            "maxAge": self.max_age_ms,
            "proxy": self.proxy,
            "location": firecrawl_location_payload(),
            "maxConcurrency": self.max_concurrency,
            "ignoreInvalidURLs": False,
        }


@dataclass(frozen=True, slots=True)
class FirecrawlPage:
    source_url: str
    title: str | None
    markdown: str | None
    raw_payload: dict[str, Any]
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FirecrawlStatus:
    provider_status: str
    total: int
    completed: int
    credits_used: int
    pages: tuple[FirecrawlPage, ...]
    next_url: str | None
    attempts: int
    retry_count: int
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FirecrawlSummary:
    run_id: str
    provider_job_id: str
    status: str
    pages_stored: int
    total: int
    completed: int
    credits_used: int
    requests_count: int
    retry_count: int
    cache_hit: bool
    started_at: datetime
    finished_at: datetime
