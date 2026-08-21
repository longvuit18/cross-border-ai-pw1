from __future__ import annotations

from typing import Any

CATEGORY_BENCHMARKS: tuple[tuple[str, float], ...] = (
    ("ornament", 0.137),
    ("christmas", 0.137),
    ("holiday", 0.135),
    ("wood", 0.130),
    ("laser cut", 0.130),
    ("jewelry", 0.200),
    ("ring", 0.190),
    ("necklace", 0.190),
    ("shirt", 0.095),
    ("hoodie", 0.095),
    ("sweatshirt", 0.095),
    ("apparel", 0.095),
    ("digital", 0.065),
    ("svg", 0.060),
    ("planner", 0.070),
    ("mug", 0.125),
    ("tumbler", 0.120),
    ("leather", 0.140),
    ("wallet", 0.135),
    ("keychain", 0.130),
)
DEFAULT_REVIEW_RATE = 0.125
DEFAULT_AGE_DAYS = 365


def estimate_sales_metrics(
    *,
    title: str,
    keyword: str,
    price_usd: float | None,
    review_count: int | None,
    age_days: int = DEFAULT_AGE_DAYS,
    views: int | None = None,
    age_source: str = "benchmark_default",
) -> dict[str, Any]:
    if age_days <= 0:
        raise ValueError("age_days must be positive")
    if price_usd is not None and price_usd < 0:
        raise ValueError("price_usd must not be negative")

    matched_category, review_rate = benchmark_for(f"{keyword} {title}")
    reviews = int(review_count or 0)
    price = float(price_usd or 0)
    if reviews <= 0:
        estimated_sales = 0
    else:
        estimated_sales = int(round(reviews / review_rate))
    estimated_revenue = round(estimated_sales * price, 2)
    monthly_sales = int(round((estimated_sales / age_days) * 30))
    monthly_revenue = round(monthly_sales * price, 2)
    conversion_rate = (
        round(estimated_sales / views, 6)
        if views is not None and views > 0
        else None
    )
    return {
        "est_sales": estimated_sales,
        "est_revenue_usd": estimated_revenue,
        "avg_monthly_sales": monthly_sales,
        "avg_monthly_revenue_usd": monthly_revenue,
        "benchmark_category": matched_category,
        "benchmark_review_rate": review_rate,
        "estimate_age_days": age_days,
        "estimate_age_source": age_source,
        "conversion_rate": conversion_rate,
    }


def benchmark_for(context: str) -> tuple[str, float]:
    normalized = " ".join(context.lower().split())
    for category_keyword, review_rate in CATEGORY_BENCHMARKS:
        if category_keyword in normalized:
            return category_keyword, review_rate
    return "default", DEFAULT_REVIEW_RATE
