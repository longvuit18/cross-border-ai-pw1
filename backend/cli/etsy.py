from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.crawler.adapters.etsy import EtsyAdapter, EtsyCredentials
from backend.crawler.errors import CrawlerError
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.service import EtsyCrawler
from backend.crawler.transport import HttpxJsonTransport
from backend.domain.models import EtsyCrawlRequest
from backend.infrastructure.sqlite_repository import SQLiteRawProductRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl active Etsy listings by keyword")
    parser.add_argument("keyword", help="Keyword or phrase to search on Etsy")
    parser.add_argument("--database", default="pw1_etsy.sqlite3", help="SQLite output path")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument(
        "--sort-on",
        default="score",
        choices=("created", "price", "updated", "score"),
    )
    parser.add_argument(
        "--sort-order",
        default="desc",
        choices=("asc", "ascending", "desc", "descending", "up", "down"),
    )
    parser.add_argument("--requests-per-second", type=float, default=2.0)
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    credentials = EtsyCredentials.from_env()
    repository = SQLiteRawProductRepository(Path(args.database))
    transport = HttpxJsonTransport(
        max_attempts=3,
        rate_limiter=AsyncRateLimiter(args.requests_per_second),
    )
    try:
        crawler = EtsyCrawler(EtsyAdapter(transport, credentials), repository)
        summary = await crawler.crawl(
            EtsyCrawlRequest(
                keyword=args.keyword,
                page_size=args.page_size,
                max_pages=args.max_pages,
                sort_on=args.sort_on,
                sort_order=args.sort_order,
            )
        )
        result = asdict(summary)
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
