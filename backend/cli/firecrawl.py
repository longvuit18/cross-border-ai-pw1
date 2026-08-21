from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.errors import CrawlerError
from backend.crawler.firecrawl_worker import FirecrawlWorker
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.transport import HttpxJsonTransport
from backend.domain.firecrawl_models import FirecrawlCrawlRequest
from backend.infrastructure.firecrawl_repository import SQLiteFirecrawlRepository
from backend.infrastructure.json_firecrawl_repository import JsonFirecrawlRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded Firecrawl v2 crawl worker")
    parser.add_argument("url", help="Absolute HTTP/HTTPS starting URL")
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument("--output-json", default="data/firecrawl.json")
    storage.add_argument("--database")
    parser.add_argument("--include-path", action="append", default=[])
    parser.add_argument("--exclude-path", action="append", default=[])
    parser.add_argument("--max-depth", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--credit-budget", type=int, default=50)
    parser.add_argument("--proxy", choices=("basic", "enhanced", "auto"), default="auto")
    parser.add_argument("--max-age-ms", type=int, default=86_400_000)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-polls", type=int, default=150)
    parser.add_argument("--cache-ttl", type=int, default=86_400)
    parser.add_argument("--force-refresh", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    transport = HttpxJsonTransport(
        max_attempts=3,
        rate_limiter=AsyncRateLimiter(2.0),
    )
    try:
        client = FirecrawlClient.from_env(transport)
        repository = (
            SQLiteFirecrawlRepository(Path(args.database))
            if args.database
            else JsonFirecrawlRepository(Path(args.output_json))
        )
        worker = FirecrawlWorker(client, repository)
        summary = await worker.run(
            FirecrawlCrawlRequest(
                url=args.url,
                include_paths=tuple(args.include_path),
                exclude_paths=tuple(args.exclude_path),
                max_discovery_depth=args.max_depth,
                limit=args.limit,
                max_age_ms=args.max_age_ms,
                proxy=args.proxy,
                credit_budget=args.credit_budget,
                poll_interval_seconds=args.poll_seconds,
                max_poll_attempts=args.max_polls,
                cache_ttl_seconds=args.cache_ttl,
            ),
            force_refresh=args.force_refresh,
        )
        result = asdict(summary)
        result["storage"] = (
            str(Path(args.database)) if args.database else str(Path(args.output_json))
        )
        return {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in result.items()
        }
    finally:
        await transport.aclose()


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except (CrawlerError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
