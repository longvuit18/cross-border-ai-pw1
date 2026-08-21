from __future__ import annotations

import pytest

from backend.estimation import benchmark_for, estimate_sales_metrics


def test_ornament_example_uses_supplied_benchmark_formula() -> None:
    result = estimate_sales_metrics(
        title="Personalized New Home Ornament",
        keyword="Christmas Ornament",
        price_usd=12.31,
        review_count=144,
        age_days=288,
        age_source="listing_created_at",
    )

    assert result == {
        "est_sales": 1051,
        "est_revenue_usd": 12937.81,
        "avg_monthly_sales": 109,
        "avg_monthly_revenue_usd": 1341.79,
        "benchmark_category": "ornament",
        "benchmark_review_rate": 0.137,
        "estimate_age_days": 288,
        "estimate_age_source": "listing_created_at",
        "conversion_rate": None,
    }


def test_default_benchmark_zero_reviews_and_conversion() -> None:
    empty = estimate_sales_metrics(
        title="Unclassified product",
        keyword="gift",
        price_usd=20,
        review_count=None,
    )
    converted = estimate_sales_metrics(
        title="Unclassified product",
        keyword="gift",
        price_usd=20,
        review_count=10,
        views=1000,
    )

    assert empty["est_sales"] == 0
    assert empty["benchmark_review_rate"] == 0.125
    assert empty["estimate_age_days"] == 365
    assert converted["conversion_rate"] == 0.08


def test_benchmark_order_matches_engine_definition() -> None:
    assert benchmark_for("digital necklace") == ("necklace", 0.190)
    assert benchmark_for("leather keychain") == ("leather", 0.140)


def test_invalid_age_is_rejected() -> None:
    with pytest.raises(ValueError, match="age_days"):
        estimate_sales_metrics(
            title="Product",
            keyword="gift",
            price_usd=10,
            review_count=1,
            age_days=0,
        )
