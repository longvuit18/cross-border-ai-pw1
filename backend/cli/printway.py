from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.crawler.adapters.printway import PrintwayCatalogClient
from backend.crawler.errors import CrawlerError
from backend.crawler.rate_limit import AsyncRateLimiter
from backend.crawler.transport import HttpxJsonTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export the public Printway catalog to JSON")
    parser.add_argument("--output", default="data/printway_catalog.json")
    parser.add_argument("--page-size", type=int, default=100)
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output)
    transport = HttpxJsonTransport(
        max_attempts=3,
        rate_limiter=AsyncRateLimiter(2.0),
    )
    try:
        catalog = await PrintwayCatalogClient(transport).fetch_catalog(
            page_size=args.page_size
        )
    finally:
        await transport.aclose()

    document = {
        "schema_version": 1,
        "source": "printway",
        "source_url": "https://printway.io/vi/all-products",
        "collected_at": datetime.now(UTC).isoformat(),
        "total_reported": catalog.total_reported,
        "products_count": len(catalog.products),
        "requests_count": catalog.requests_count,
        "products": list(catalog.products),
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
        "output": str(output),
        "products_count": len(catalog.products),
        "requests_count": catalog.requests_count,
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
