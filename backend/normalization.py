from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.estimation import estimate_sales_metrics

PRICE_PATTERN = re.compile(r"NowPrice:\$([\d,.]+)", re.IGNORECASE)
SHOP_PATTERN = re.compile(
    r"\[([^\]]+)\]\(https://www\.etsy\.com/shop/[^)]+\)",
    re.IGNORECASE,
)
RATING_PATTERN = re.compile(r"\[([\d.]+) out of 5 stars\]", re.IGNORECASE)
ITEM_RATING_PATTERN = re.compile(
    r"([\d.]+)\s*\n+\s*Item average",
    re.IGNORECASE,
)
REVIEW_COUNT_PATTERN = re.compile(r"\(([\d,.]+[kK]?) reviews\)", re.IGNORECASE)
FAVORITES_PATTERN = re.compile(r"has ([\d,]+) favorites", re.IGNORECASE)
SHIPS_FROM_PATTERN = re.compile(r"Ships from ([^.]+)", re.IGNORECASE)
MATERIALS_PATTERN = re.compile(r"^- Materials:\s*(.+)$", re.MULTILINE)


def load_json(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return document


def normalize_printway(document: dict[str, Any]) -> list[dict[str, Any]]:
    products = document.get("products")
    if not isinstance(products, list):
        return []
    normalized: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        slug = _text(product.get("slug"))
        prices = product.get("displayPrice")
        prices = prices if isinstance(prices, dict) else {}
        numeric_prices = [
            float(value)
            for value in prices.values()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        locations = product.get("location")
        locations = locations if isinstance(locations, list) else []
        categories = product.get("categories")
        categories = categories if isinstance(categories, list) else []
        options = product.get("option")
        options = options if isinstance(options, list) else []
        normalized.append(
            {
                "source": "Printway",
                "product_id": _text(product.get("_id")),
                "product_code": _text(product.get("productCode")),
                "name": _text(product.get("name")),
                "image_url": _text(product.get("mockupUrls")),
                "starting_price_usd": min(numeric_prices) if numeric_prices else None,
                "titanium_price_usd": _number(product.get("minTitanium")),
                "category": ", ".join(
                    _text(value.get("name"))
                    for value in categories
                    if isinstance(value, dict) and value.get("name")
                ),
                "production": ", ".join(
                    _text(value.get("name"))
                    for value in locations
                    if isinstance(value, dict) and value.get("name")
                ),
                "options": ", ".join(
                    _option_summary(value)
                    for value in options
                    if isinstance(value, dict)
                ),
                "url": f"https://printway.io/vi/product/{slug}" if slug else "",
            }
        )
    return normalized


def normalize_etsy(document: dict[str, Any]) -> list[dict[str, Any]]:
    pages = document.get("pages")
    if not isinstance(pages, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    run = document.get("run")
    run = run if isinstance(run, dict) else {}
    keyword = _text(run.get("keyword"))
    for page in pages:
        if not isinstance(page, dict) or page.get("page_type") != "listing_detail":
            continue
        listing_id = _text(page.get("listing_id"))
        if not listing_id or listing_id in seen:
            continue
        raw = page.get("raw_payload")
        raw = raw if isinstance(raw, dict) else {}
        metadata = raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        markdown = _text(page.get("markdown"))
        description = _text(
            metadata.get("og:description")
            or metadata.get("ogDescription")
            or metadata.get("description")
        )
        current_price = _match_number(PRICE_PATTERN, markdown)
        original_price = _number(metadata.get("product:price:amount"))
        item_reviews = _item_reviews_block(markdown)
        title = _clean_etsy_title(
            _text(metadata.get("og:title") or metadata.get("title") or page.get("title"))
        )
        price = current_price if current_price is not None else original_price
        review_count = _match_compact_integer(REVIEW_COUNT_PATTERN, item_reviews)
        listing = {
                "source": "Etsy",
                "listing_id": listing_id,
                "title": title,
                "image_url": _text(metadata.get("og:image") or metadata.get("ogImage")),
                "price": price,
                "original_price": original_price,
                "currency": _text(metadata.get("product:price:currency")) or "USD",
                "shop": _match_text(SHOP_PATTERN, markdown),
                "rating": _match_number(ITEM_RATING_PATTERN, item_reviews)
                or _match_number(RATING_PATTERN, markdown),
                "review_count": review_count,
                "favorites": _match_integer(FAVORITES_PATTERN, description),
                "ships_from": _match_text(SHIPS_FROM_PATTERN, description),
                "materials": _match_text(MATERIALS_PATTERN, markdown),
                "personalizable": "Add personalization" in markdown,
                "description": description,
                "url": _text(page.get("source_url") or metadata.get("sourceURL")),
                "scraped_at": _text(page.get("observed_at")),
            }
        listing.update(
            estimate_sales_metrics(
                title=title,
                keyword=keyword,
                price_usd=price,
                review_count=review_count,
            )
        )
        normalized.append(listing)
        seen.add(listing_id)
    return normalized


def build_normalized_document(
    printway_document: dict[str, Any],
    etsy_document: dict[str, Any],
) -> dict[str, Any]:
    printway = normalize_printway(printway_document)
    etsy = normalize_etsy(etsy_document)
    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "printway_products": len(printway),
            "etsy_listings": len(etsy),
        },
        "printway_products": printway,
        "etsy_listings": etsy,
    }


def write_normalized_document(document: dict[str, Any], output: str | Path) -> None:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(target)


def _option_summary(option: dict[str, Any]) -> str:
    label = _text(option.get("label"))
    values = option.get("values")
    values = values if isinstance(values, list) else []
    count = len([value for value in values if isinstance(value, dict)])
    return f"{label} ({count})" if label else ""


def _clean_etsy_title(value: str) -> str:
    return re.sub(r"\s+- Etsy$", "", value).strip()


def _match_text(pattern: re.Pattern[str], value: str) -> str:
    match = pattern.search(value)
    return match.group(1).strip() if match else ""


def _match_number(pattern: re.Pattern[str], value: str) -> float | None:
    match = pattern.search(value)
    return _number(match.group(1).replace(",", "")) if match else None


def _match_integer(pattern: re.Pattern[str], value: str) -> int | None:
    match = pattern.search(value)
    return int(match.group(1).replace(",", "")) if match else None


def _match_compact_integer(pattern: re.Pattern[str], value: str) -> int | None:
    match = pattern.search(value)
    if not match:
        return None
    compact = match.group(1).replace(",", "").lower()
    multiplier = 1_000 if compact.endswith("k") else 1
    numeric = compact[:-1] if multiplier == 1_000 else compact
    return round(float(numeric) * multiplier)


def _item_reviews_block(markdown: str) -> str:
    marker = "## Reviews for this item"
    start = markdown.find(marker)
    if start < 0:
        return ""
    remaining = markdown[start + len(marker) :]
    end_match = re.search(r"^##\s+", remaining, re.MULTILINE)
    return remaining[: end_match.start()] if end_match else remaining


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
