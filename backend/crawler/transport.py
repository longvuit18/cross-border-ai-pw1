from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from backend.crawler.errors import (
    AuthenticationError,
    MalformedResponseError,
    RateLimitError,
    RequestRejectedError,
    UpstreamError,
)
from backend.crawler.rate_limit import NoopRateLimiter


class RateLimiter(Protocol):
    async def acquire(self) -> None: ...


@dataclass(frozen=True, slots=True)
class JsonResponse:
    status_code: int
    headers: dict[str, str]
    payload: dict[str, Any]
    attempts: int

    @property
    def retry_count(self) -> int:
        return self.attempts - 1


class JsonTransport(Protocol):
    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
    ) -> JsonResponse: ...

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
    ) -> JsonResponse: ...


class HttpxJsonTransport:
    RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 30.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
        )
        self._max_attempts = max_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._sleeper = sleeper
        self._jitter = jitter
        self._rate_limiter = rate_limiter or NoopRateLimiter()

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str | int],
        headers: Mapping[str, str],
    ) -> JsonResponse:
        return await self.request_json(
            "GET",
            url,
            params=params,
            headers=headers,
        )

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
    ) -> JsonResponse:
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            await self._rate_limiter.acquire()
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    json=json_body,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    raise UpstreamError("Upstream request failed after network retries") from exc
                await self._sleeper(self._backoff_seconds(attempt, None))
                continue

            normalized_headers = {key.lower(): value for key, value in response.headers.items()}
            if response.status_code in {401, 403}:
                raise AuthenticationError("Upstream rejected the configured API credentials")

            if response.status_code in self.RETRYABLE_STATUSES:
                if attempt == self._max_attempts:
                    if response.status_code == 429:
                        raise RateLimitError("Upstream rate limit remained exhausted after retries")
                    raise UpstreamError(
                        f"Upstream returned HTTP {response.status_code} after retries"
                    )
                await self._sleeper(
                    self._backoff_seconds(attempt, normalized_headers.get("retry-after"))
                )
                continue

            if response.status_code >= 400:
                raise RequestRejectedError(
                    f"Upstream rejected request with HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise MalformedResponseError("Upstream returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise MalformedResponseError("Upstream response root must be a JSON object")
            return JsonResponse(
                status_code=response.status_code,
                headers=normalized_headers,
                payload=payload,
                attempts=attempt,
            )

        raise UpstreamError("Upstream request failed") from last_error

    def _backoff_seconds(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return min(self._max_backoff_seconds, max(0.0, float(retry_after)))
            except ValueError:
                pass
        exponential = self._base_backoff_seconds * (2 ** (attempt - 1))
        return min(self._max_backoff_seconds, exponential + self._jitter() * exponential)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
