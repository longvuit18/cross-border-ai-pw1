from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class AsyncRateLimiter:
    """Simple per-process limiter used before each upstream request."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._minimum_interval = 1 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed_at - now)
            if delay:
                await self._sleeper(delay)
                now = self._clock()
            self._next_allowed_at = max(now, self._next_allowed_at) + self._minimum_interval


class NoopRateLimiter:
    async def acquire(self) -> None:
        return None

