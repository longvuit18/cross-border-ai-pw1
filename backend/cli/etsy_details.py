from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.errors import CrawlerError
from backend.crawler.etsy_detail_worker import EtsyDetailWorker
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.transport import HttpxJsonTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume an Etsy search JSON by batch-scraping listing details"
    )
    parser.add_argument("json_file")
    parser.add_argument("--max-listings", type=int, default=10)
    parser.add_argument("--max-concurrency", type=int, default=2)
    parser.add_argument("--proxy", choices=("basic", "enhanced", "auto"), default="auto")
    parser.add_argument("--credit-budget", type=int, default=50)
    parser.add_argument("--max-age-ms", type=int, default=86_400_000)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-polls", type=int, default=150)
    return parser


async def run(args: argparse.Namespace) -> dict[str, object]:
    transport = HttpxJsonTransport(
        max_attempts=3,
        rate_limiter=AsyncRateLimiter(2.0),
    )
    try:
        summary = await EtsyDetailWorker(
            FirecrawlClient.from_env(transport),
            args.json_file,
        ).run(
            max_listings=args.max_listings,
            max_concurrency=args.max_concurrency,
            proxy=args.proxy,
            max_age_ms=args.max_age_ms,
            credit_budget=args.credit_budget,
            poll_interval_seconds=args.poll_seconds,
            max_poll_attempts=args.max_polls,
        )
        return asdict(summary)
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
