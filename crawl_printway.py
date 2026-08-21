#!/usr/bin/env python3
"""Crawl the full public Printway.io catalog into structured files.

Sources (all public, unauthenticated):
  - https://printway.io/sitemap-product.xml      product URL list
  - https://apis.printway.io/v1/...              the JSON API the site's own frontend calls

Outputs (data/printway/):
  raw/detail/<slug>.json   raw API payload per product
  categories.json          category tree + flat list
  products.json            normalised product records
  products.csv             flat table for spreadsheets
  crawl_report.json        run stats, misses, errors
"""

from __future__ import annotations

import csv
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from threading import Lock

API = "https://apis.printway.io/v1"
SITE = "https://printway.io"
OUT = Path(__file__).parent / "data" / "printway"
RAW = OUT / "raw" / "detail"

# The API rejects requests without a browser-ish Origin/Referer pair; these are
# the same headers the printway.io frontend sends for these public endpoints.
HEADERS = {
    "Origin": SITE,
    "Referer": SITE + "/",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

CONCURRENCY = 6      # keep the load on their origin modest
DELAY = 0.12         # per-request politeness pause
RETRIES = 3


def _ssl_context() -> ssl.SSLContext:
    """python.org builds on macOS ship no CA store; fall back to the system one."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    for path in ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem"):
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


SSL_CTX = _ssl_context()

_print_lock = Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


def get(url: str, timeout: int = 45) -> bytes:
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed after {RETRIES} tries: {last}")


def get_json(url: str) -> dict:
    return json.loads(get(url).decode("utf-8"))


def strip_html(html: str | None) -> str:
    """Collapse an HTML description into readable plain text."""
    if not html:
        return ""
    text = re.sub(r"<(br|/p|/li|/h[1-6]|/tr)\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# 1. discovery
# --------------------------------------------------------------------------- #

def sitemap_slugs() -> list[str]:
    xml = get(f"{SITE}/sitemap-product.xml").decode("utf-8")
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)
    return [u.rstrip("/").rsplit("/", 1)[-1] for u in locs if "/product/" in u]


def sitemap_category_urls() -> list[str]:
    xml = get(f"{SITE}/sitemap-collection.xml").decode("utf-8")
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def fetch_catalog() -> list[dict]:
    payload = get_json(f"{API}/product-type/home/catalog")
    if not payload.get("success"):
        raise RuntimeError(f"catalog endpoint failed: {payload}")
    return payload["data"]


def fetch_taxonomy() -> dict:
    out = {}
    for key, path in [
        ("category_tree", "category/category-home"),
        ("category_flat", "category-home-flatten"),
        ("locations", "location-category-all"),
        ("production_techniques", "production-technique-all"),
    ]:
        try:
            out[key] = get_json(f"{API}/{path}").get("data")
        except Exception as exc:  # noqa: BLE001
            log(f"  ! taxonomy {path}: {exc}")
            out[key] = None
    return out


# --------------------------------------------------------------------------- #
# 2. per-product detail
# --------------------------------------------------------------------------- #

def fetch_detail(slug: str) -> tuple[str, dict | None, str | None]:
    cached = RAW / f"{slug}.json"
    if cached.exists():
        try:
            return slug, json.loads(cached.read_text("utf-8")), None
        except json.JSONDecodeError:
            cached.unlink()
    time.sleep(DELAY)
    url = f"{API}/product-type/home/detail-product?slug={urllib.parse.quote(slug)}"
    try:
        payload = get_json(url)
    except Exception as exc:  # noqa: BLE001
        return slug, None, str(exc)
    data = payload.get("data")
    if data is None:
        # slug present in the sitemap but no longer served (retired product)
        return slug, None, "not_found"
    cached.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return slug, data, None


# --------------------------------------------------------------------------- #
# 3. normalisation
# --------------------------------------------------------------------------- #

def normalise(slug: str, detail: dict, listing: dict | None) -> dict:
    cats = detail.get("categories") or []
    primary = cats[0] if cats else {}
    parent = primary.get("parentId") or {}
    if isinstance(parent, str):
        parent = {}

    options = []
    for opt in detail.get("option") or []:
        options.append({
            "label": opt.get("label"),
            "option_name": opt.get("optionName"),
            "values": [v.get("name") for v in (opt.get("values") or [])],
            "value_count": len(opt.get("values") or []),
        })

    locations, suppliers = [], set()
    for loc in detail.get("location") or []:
        locations.append({
            "code": loc.get("code"),
            "name": loc.get("name"),
            "default": loc.get("default"),
            "tiktok_enabled": loc.get("isEnabledTiktok"),
            "min_diamond_price": loc.get("minDiamondPrice"),
            "min_titanium_price": loc.get("minTitaniumPrice"),
        })
        for sup in loc.get("suppliers") or []:
            if sup.get("name"):
                suppliers.add(sup["name"])

    proc = detail.get("processingTime") or {}
    variant_combos = 1
    for opt in options:
        variant_combos *= max(1, opt["value_count"])

    return {
        "id": detail.get("_id"),
        "slug": slug,
        "url": f"{SITE}/product/{slug}",
        "name": detail.get("name"),
        "product_code": detail.get("productCode"),
        "categories": [
            {"name": c.get("name"), "slug": c.get("slug")} for c in cats
        ],
        "category": primary.get("name"),
        "category_slug": primary.get("slug"),
        "parent_category": parent.get("name"),
        "parent_category_slug": parent.get("slug"),
        "price_min_diamond": detail.get("minDiamond"),
        "price_min_titanium": detail.get("minTitanium"),
        "price_max": detail.get("maxPrice"),
        "display_price": detail.get("displayPrice") or {},
        "currency": "USD",
        "processing_time_days_from": proc.get("from"),
        "processing_time_days_to": proc.get("to"),
        "fulfil_locations": locations,
        "suppliers": sorted(suppliers),
        "options": options,
        "variant_combinations": variant_combos,
        "image_count": len(detail.get("mockupUrls") or []),
        "images": detail.get("mockupUrls") or [],
        "video_urls": detail.get("videoUrls") or "",
        "template_artworks_url": detail.get("templateArtworksUrl"),
        "google_indexed": detail.get("googleIndex"),
        "is_deleted": detail.get("isDeleted"),
        "description_text": strip_html(detail.get("description")),
        "description_html": detail.get("description") or "",
        "print_guideline_text": strip_html(detail.get("guideLine")),
        "in_catalog_listing": listing is not None,
    }


CSV_COLUMNS = [
    "product_code", "name", "slug", "url", "parent_category", "category",
    "price_min_diamond", "price_min_titanium", "price_max",
    "processing_time_days_from", "processing_time_days_to",
    "fulfil_location_codes", "suppliers", "option_labels",
    "variant_combinations", "image_count",
]


def csv_row(p: dict) -> dict:
    return {
        "product_code": p["product_code"],
        "name": p["name"],
        "slug": p["slug"],
        "url": p["url"],
        "parent_category": p["parent_category"],
        "category": p["category"],
        "price_min_diamond": p["price_min_diamond"],
        "price_min_titanium": p["price_min_titanium"],
        "price_max": p["price_max"],
        "processing_time_days_from": p["processing_time_days_from"],
        "processing_time_days_to": p["processing_time_days_to"],
        "fulfil_location_codes": "|".join(
            l["code"] for l in p["fulfil_locations"] if l.get("code")
        ),
        "suppliers": "|".join(p["suppliers"]),
        "option_labels": "|".join(o["label"] or "" for o in p["options"]),
        "variant_combinations": p["variant_combinations"],
        "image_count": p["image_count"],
    }


# --------------------------------------------------------------------------- #

def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    started = time.time()

    log("[1/5] discovery …")
    listing = fetch_catalog()
    log(f"      catalog endpoint: {len(listing)} products")
    sm = sitemap_slugs()
    log(f"      sitemap:          {len(sm)} product urls")

    by_slug = {p["slug"]: p for p in listing}
    slugs = list(by_slug) + [s for s in sm if s not in by_slug]
    log(f"      union to crawl:   {len(slugs)}")

    log("[2/5] taxonomy …")
    taxonomy = fetch_taxonomy()
    taxonomy["category_urls_from_sitemap"] = sitemap_category_urls()
    (OUT / "categories.json").write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2), "utf-8"
    )

    log(f"[3/5] product details ({CONCURRENCY} workers) …")
    details, missing, errors = {}, [], {}
    done = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        for slug, data, err in pool.map(fetch_detail, slugs):
            done += 1
            if done % 100 == 0 or done == len(slugs):
                log(f"      {done}/{len(slugs)}")
            if err == "not_found":
                missing.append(slug)
            elif err:
                errors[slug] = err
            else:
                details[slug] = data

    log(f"      ok={len(details)} retired={len(missing)} errors={len(errors)}")

    log("[4/5] normalising …")
    products = [
        normalise(s, details[s], by_slug.get(s))
        for s in slugs if s in details
    ]
    products.sort(key=lambda p: ((p["parent_category"] or "~"),
                                 (p["category"] or "~"),
                                 (p["name"] or "")))
    (OUT / "products.json").write_text(
        json.dumps(products, ensure_ascii=False, indent=2), "utf-8"
    )

    with (OUT / "products.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for p in products:
            w.writerow(csv_row(p))

    log("[5/5] report …")
    prices = [p["price_min_diamond"] for p in products
              if isinstance(p["price_min_diamond"], (int, float))]
    cat_counts: dict[str, int] = {}
    for p in products:
        key = f'{p["parent_category"] or "?"} / {p["category"] or "?"}'
        cat_counts[key] = cat_counts.get(key, 0) + 1

    report = {
        "source": SITE,
        "api_base": API,
        "crawled_at_unix": int(started),
        "duration_seconds": round(time.time() - started, 1),
        "counts": {
            "catalog_endpoint": len(listing),
            "sitemap_urls": len(sm),
            "attempted": len(slugs),
            "products_captured": len(products),
            "sitemap_only_retired": len(missing),
            "errors": len(errors),
            "categories_from_sitemap": len(taxonomy["category_urls_from_sitemap"]),
            "total_variant_combinations": sum(p["variant_combinations"] for p in products),
            "total_images": sum(p["image_count"] for p in products),
        },
        "price_usd_min_diamond": {
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
            "avg": round(sum(prices) / len(prices), 2) if prices else None,
        },
        "products_per_category": dict(sorted(cat_counts.items(),
                                             key=lambda kv: -kv[1])),
        "retired_slugs_in_sitemap": sorted(missing),
        "errors": errors,
        "not_captured": {
            "variant_level_prices": "endpoint /product-type/home/get-price-variant "
                                    "returns 403 Forbidden without a seller login",
        },
    }
    (OUT / "crawl_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )

    log(f"\nDone in {report['duration_seconds']}s → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
