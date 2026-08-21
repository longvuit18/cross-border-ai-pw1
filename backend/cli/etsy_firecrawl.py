from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

from backend.crawler.adapters.firecrawl import FirecrawlClient
from backend.crawler.errors import CrawlerError
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.transport import HttpxJsonTransport
from backend.domain.firecrawl_models import (
    FirecrawlScrapeRequest,
    firecrawl_location_payload,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape one Etsy keyword page into JSON")
    parser.add_argument("keyword")
    parser.add_argument("--output")
    parser.add_argument("--proxy", choices=("basic", "enhanced", "auto"), default="auto")
    parser.add_argument("--credit-budget", type=int, default=5)
    parser.add_argument("--max-age-ms", type=int, default=86_400_000)
    parser.add_argument("--force-refresh", action="store_true")
    return parser


def output_path(keyword: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    slug = re.sub(r"[^a-z0-9]+", "_", keyword.lower()).strip("_") or "keyword"
    return Path("data") / f"etsy_{slug}.json"


async def run(args: argparse.Namespace) -> dict[str, object]:
    keyword = args.keyword.strip()
    if not keyword:
        raise ValueError("keyword must not be empty")
    output = output_path(keyword, args.output)
    start_url = f"https://www.etsy.com/search?q={quote_plus(keyword)}"
    request = FirecrawlScrapeRequest(
        url=start_url,
        max_age_ms=0 if args.force_refresh else args.max_age_ms,
        proxy=args.proxy,
        credit_budget=args.credit_budget,
    )
    started_at = datetime.now(UTC)
    transport = HttpxJsonTransport(
        max_attempts=3,
        rate_limiter=AsyncRateLimiter(2.0),
    )
    try:
        page, response = await FirecrawlClient.from_env(transport).scrape(request)
    finally:
        await transport.aclose()
    finished_at = datetime.now(UTC)
    document = {
        "schema_version": 1,
        "source": "firecrawl",
        "run": {
            "type": "scrape",
            "status": "succeeded",
            "start_url": start_url,
            "keyword": keyword,
            "pages_stored": 1,
            "credits_used": None,
            "credit_budget": args.credit_budget,
            "location": firecrawl_location_payload(),
            "requests_count": response.attempts,
            "retry_count": response.retry_count,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        },
        "pages": [
            {
                "source_url": page.source_url,
                "title": page.title,
                "markdown": page.markdown,
                "raw_payload": page.raw_payload,
                "observed_at": page.observed_at.isoformat(),
            }
        ],
        "provider_response": response.payload,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(output)
    return {
        "status": "succeeded",
        "keyword": keyword,
        "pages_stored": 1,
        "output": str(output),
        "maximum_planned_credits": request.maximum_planned_credits,
    }


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
