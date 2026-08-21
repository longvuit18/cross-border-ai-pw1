from __future__ import annotations


class CrawlerError(Exception):
    """Base error for predictable crawler failures."""


class ConfigurationError(CrawlerError):
    """Required source configuration is missing or invalid."""


class AuthenticationError(CrawlerError):
    """The upstream rejected source credentials."""


class RateLimitError(CrawlerError):
    """The upstream rate limit remained exhausted after retries."""


class UpstreamError(CrawlerError):
    """The upstream returned a retryable server error repeatedly."""


class RequestRejectedError(CrawlerError):
    """The upstream rejected a non-retryable request."""


class MalformedResponseError(CrawlerError):
    """The upstream response could not be parsed safely."""


class PaginationError(CrawlerError):
    """Pagination did not make forward progress."""


class CreditBudgetExceeded(CrawlerError):
    """A provider crawl exceeded its configured credit budget."""


class CrawlTimeoutError(CrawlerError):
    """A provider crawl did not reach a terminal state in time."""

