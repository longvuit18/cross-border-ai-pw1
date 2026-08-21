from __future__ import annotations

import httpx
import pytest

from backend.crawler.errors import (
    AuthenticationError,
    MalformedResponseError,
    RequestRejectedError,
    UpstreamError,
)
from backend.crawler.transport import HttpxJsonTransport


async def test_retries_429_and_honors_retry_after() -> None:
    statuses = iter([429, 200])
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        if status == 429:
            return httpx.Response(status, headers={"retry-after": "2"}, json={"error": "slow"})
        return httpx.Response(status, json={"count": 0, "results": []})

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client, sleeper=sleeper, jitter=lambda: 0)
        response = await transport.get_json("https://example.test", params={}, headers={})

    assert calls == 2
    assert sleeps == [2.0]
    assert response.attempts == 2
    assert response.retry_count == 1


async def test_retries_request_timeout_status() -> None:
    statuses = iter([408, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json={"status": "completed"})

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(
            client=client,
            sleeper=sleeper,
            jitter=lambda: 0,
        )
        response = await transport.get_json("https://example.test", params={}, headers={})

    assert response.attempts == 2
    assert sleeps == [0.5]


async def test_authentication_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "invalid key"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client)
        with pytest.raises(AuthenticationError):
            await transport.get_json("https://example.test", params={}, headers={})

    assert calls == 1


async def test_bad_request_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "bad request"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client)
        with pytest.raises(RequestRejectedError, match="400"):
            await transport.get_json("https://example.test", params={}, headers={})


async def test_invalid_json_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client)
        with pytest.raises(MalformedResponseError):
            await transport.get_json("https://example.test", params={}, headers={})


async def test_network_failure_is_retried_until_exhausted() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(
            client=client,
            max_attempts=3,
            sleeper=sleeper,
            jitter=lambda: 0,
        )
        with pytest.raises(UpstreamError):
            await transport.get_json("https://example.test", params={}, headers={})

    assert calls == 3
    assert sleeps == [0.5, 1.0]


async def test_request_json_supports_post_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "job-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        transport = HttpxJsonTransport(client=client)
        response = await transport.request_json(
            "POST",
            "https://example.test/v2/crawl",
            headers={"authorization": "Bearer secret"},
            json_body={"url": "https://example.com"},
        )

    assert captured["method"] == "POST"
    assert b'"url":"https://example.com"' in captured["body"]
    assert response.payload["id"] == "job-1"
