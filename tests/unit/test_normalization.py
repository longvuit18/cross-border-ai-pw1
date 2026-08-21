from __future__ import annotations

from backend.normalization import build_normalized_document, normalize_etsy, normalize_printway


def test_printway_normalization_keeps_only_decision_fields() -> None:
    values = normalize_printway(
        {
            "products": [
                {
                    "_id": "pw-1",
                    "name": "Ornament",
                    "slug": "ornament",
                    "productCode": "ORN",
                    "mockupUrls": "https://cdn.example/image.jpg",
                    "displayPrice": {"VN": 6.0, "US": 8.0},
                    "minTitanium": 6.5,
                    "categories": [{"name": "Ornaments"}],
                    "location": [{"name": "Made in VN"}],
                    "option": [{"label": "Size", "values": [{"name": "4 inch"}]}],
                    "variants": "raw-not-needed",
                }
            ]
        }
    )

    assert values == [
        {
            "source": "Printway",
            "product_id": "pw-1",
            "product_code": "ORN",
            "name": "Ornament",
            "image_url": "https://cdn.example/image.jpg",
            "starting_price_usd": 6.0,
            "titanium_price_usd": 6.5,
            "category": "Ornaments",
            "production": "Made in VN",
            "options": "Size (1)",
            "url": "https://printway.io/vi/product/ornament",
        }
    ]


def test_etsy_normalization_extracts_price_shop_rating_and_merchandising_fields() -> None:
    values = normalize_etsy(
        {
            "pages": [
                {
                    "page_type": "listing_detail",
                    "listing_id": "123",
                    "source_url": "https://www.etsy.com/listing/123/test",
                    "observed_at": "2026-08-21T00:00:00+00:00",
                    "markdown": """
NowPrice:$12.50+
# Personalized Ornament
[DemoShop](https://www.etsy.com/shop/DemoShop?ref=x)
[4.8 out of 5 stars](https://www.etsy.com/listing/123/test#reviews)
Add personalization
- Materials: wood, ribbon
## Reviews for this item
4.7
Item average
(1.3k reviews)
## More from this shop
""",
                    "raw_payload": {
                        "metadata": {
                            "og:title": "Personalized Ornament - Etsy",
                            "og:image": "https://img.example/ornament.jpg",
                            "og:description": (
                                "This item has 1,234 favorites from Etsy shoppers. "
                                "Ships from Vietnam. Listed today"
                            ),
                            "product:price:amount": "15.00",
                            "product:price:currency": "USD",
                        }
                    },
                }
            ]
        }
    )

    assert values[0]["price"] == 12.5
    assert values[0]["original_price"] == 15.0
    assert values[0]["shop"] == "DemoShop"
    assert values[0]["rating"] == 4.7
    assert values[0]["review_count"] == 1300
    assert values[0]["favorites"] == 1234
    assert values[0]["ships_from"] == "Vietnam"
    assert values[0]["materials"] == "wood, ribbon"
    assert values[0]["personalizable"] is True
    assert values[0]["benchmark_category"] == "ornament"
    assert values[0]["benchmark_review_rate"] == 0.137
    assert values[0]["est_sales"] == 9489
    assert values[0]["avg_monthly_sales"] == 780
    assert values[0]["estimate_age_source"] == "benchmark_default"
    assert "csrf_nonce" not in values[0]


def test_combined_document_reports_normalized_counts() -> None:
    document = build_normalized_document({"products": []}, {"pages": []})
    assert document["schema_version"] == 2
    assert document["summary"] == {"printway_products": 0, "etsy_listings": 0}
