#!/usr/bin/env python3
"""Scrape / crawl san pham Amazon + Etsy qua Firecrawl API v2.

Dung:
  python3 firecrawl_shop.py discover amazon "echo dot" -n 5
  python3 firecrawl_shop.py discover etsy "handmade ceramic mug" -n 5
  python3 firecrawl_shop.py product <url> [<url> ...]
  python3 firecrawl_shop.py run "echo dot" "handmade ceramic mug" -n 3   # discover + scrape ca 2 san
"""
import argparse, csv, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
API = "https://api.firecrawl.dev/v2"


def api_key():
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:  # doc .env thu cong, khong can python-dotenv
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("FIRECRAWL_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("'\"")
    if not key:
        sys.exit("Thieu FIRECRAWL_API_KEY (env hoac .env)")
    return key


def site_of(url):
    return "amazon" if "amazon." in url else "etsy" if "etsy." in url else "unknown"


HEADERS = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}

# Amazon chan bot rat manh => bat buoc stealth (5 credits/page).
# Etsy qua duoc voi proxy basic (1 credit/page); chi fallback stealth khi that bai.
BASE_OPTS = {
    "onlyMainContent": True,
    "waitFor": 4000,
    "timeout": 90000,
    "blockAds": True,
    "headers": {"Accept-Language": "en-US,en;q=0.9"},
}
PROXY = {"amazon": "stealth", "etsy": "basic", "unknown": "basic"}

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "brand_or_shop": {"type": "string", "description": "Brand (Amazon) hoac ten shop (Etsy)"},
        "price": {"type": "number", "description": "Gia hien tai, chi so"},
        "currency": {"type": "string"},
        "original_price": {"type": ["number", "null"], "description": "Gia goc truoc giam, null neu khong co"},
        "rating": {"type": ["number", "null"], "description": "Diem danh gia 0-5"},
        "review_count": {"type": ["integer", "null"]},
        "availability": {"type": "string", "description": "In stock / Out of stock / ..."},
        "image_url": {"type": ["string", "null"], "description": "Anh chinh"},
        "bullets": {"type": "array", "items": {"type": "string"}, "description": "Cac dac diem chinh, toi da 6"},
        "description": {"type": "string", "description": "Mo ta ngan, <= 300 ky tu"},
        # cac field duoi chu yeu co tren Etsy
        "materials": {"type": "array", "items": {"type": "string"}, "description": "Chat lieu (Etsy)"},
        "dimensions": {"type": ["string", "null"], "description": "Kich thuoc neu co ghi"},
        "is_bestseller": {"type": ["boolean", "null"], "description": "Co badge Bestseller/Popular now khong"},
        "favorites": {"type": ["integer", "null"], "description": "So luot favorite / 'X people have this in their cart'"},
        "ships_from": {"type": ["string", "null"], "description": "Noi gui hang"},
        "discount_percent": {"type": ["number", "null"], "description": "% giam gia neu dang sale"},
    },
    "required": ["title", "price"],
}

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string", "description": "URL day du toi trang san pham"},
                    "price": {"type": ["number", "null"]},
                    "rating": {"type": ["number", "null"]},
                    "review_count": {"type": ["integer", "null"]},
                },
                "required": ["title", "url"],
            },
        }
    },
    "required": ["products"],
}


def post(path, payload, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.post(f"{API}{path}", headers=HEADERS, json=payload, timeout=180)
            body = r.json()
            if body.get("success"):
                return body
            last = body.get("error") or body
        except Exception as e:  # timeout / JSON loi
            last = str(e)
        time.sleep(2 * (i + 1))
    return {"success": False, "error": last}


def scrape(url, formats, **extra):
    """Thu proxy mac dinh cua site truoc; neu fail/bi chan thi retry bang stealth."""
    site = site_of(url)
    opts = dict(BASE_OPTS, url=url, formats=formats, proxy=PROXY[site])
    opts.update(extra)
    res = post("/scrape", opts)
    blocked = not res.get("success") or ((res.get("data", {}).get("metadata") or {}).get("statusCode") in (403, 404, 429, 503))
    if blocked and opts["proxy"] != "stealth":
        opts["proxy"] = "stealth"
        res = post("/scrape", opts)
    return res


def json_fmt(schema, prompt):
    return {"type": "json", "schema": schema, "prompt": prompt}


# ---------- discover ----------
SEARCH_URL = {
    "amazon": lambda q, pg: f"https://www.amazon.com/s?k={requests.utils.quote(q)}&page={pg}",
    "etsy": lambda q, pg: f"https://www.etsy.com/search?q={requests.utils.quote(q)}&page={pg}",
}
LISTING_RE = {
    "amazon": re.compile(r"https://www\.amazon\.com/[^)\s\"]*?/dp/([A-Z0-9]{10})"),
    "etsy": re.compile(r"https://www\.etsy\.com/listing/(\d+)/[a-z0-9\-]+"),
}


def discover(site, query, n, pages=1):
    """Tra ve list dict {url, id, title, price, rating, review_count}."""
    items, seen = [], set()
    for pg in range(1, pages + 1):
        for it in _discover_page(site, query, pg):
            if it["id"] in seen:
                continue
            seen.add(it["id"])
            items.append(it)
        if len(items) >= n:
            break
    return items[:n]


def _discover_page(site, query, pg):
    url = SEARCH_URL[site](query, pg)
    res = scrape(url, ["markdown", "links", json_fmt(SEARCH_SCHEMA, f"Liet ke cac san pham trong ket qua tim kiem '{query}'. Bo qua quang cao va noi dung khong phai san pham.")])
    if not res.get("success"):
        print(f"  ! discover {site} p{pg} that bai: {res.get('error')}", file=sys.stderr)
        return []
    data = res["data"]
    items, seen = [], set()

    # nguon 1: JSON extraction (co ca gia/rating)
    for p in (data.get("json") or {}).get("products", []):
        m = LISTING_RE[site].search(p.get("url", ""))
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        items.append({"site": site, "id": m.group(1), "url": m.group(0), "search_title": p.get("title"),
                      "search_price": p.get("price"), "search_rating": p.get("rating"),
                      "search_review_count": p.get("review_count")})

    # nguon 2: fallback regex tren markdown + links neu JSON thieu
    blob = (data.get("markdown") or "") + "\n" + "\n".join(data.get("links") or [])
    for m in LISTING_RE[site].finditer(blob):
        if m.group(1) in seen:
            continue
        seen.add(m.group(1))
        items.append({"site": site, "id": m.group(1), "url": m.group(0), "search_title": None,
                      "search_price": None, "search_rating": None, "search_review_count": None})

    return items


# ---------- shop ----------
def shop_listings(base_url, n=10, pages=3):
    """Etsy: gom URL listing tu trang shop HOAC trang category, di qua tung trang phan trang.

    Dung cho ca /shop/<ten> va /c/home-and-living/home-decor -- cung cau truc link /listing/<id>/<slug>.
    """
    out, seen = [], set()
    for pg in range(1, pages + 1):
        sep = "&" if "?" in base_url else "?"
        res = scrape(f"{base_url}{sep}page={pg}", ["markdown", "links"])
        if not res.get("success"):
            continue
        blob = (res["data"].get("markdown") or "") + "\n" + "\n".join(res["data"].get("links") or [])
        for m in LISTING_RE["etsy"].finditer(blob):
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
            out.append(m.group(0))
        if len(out) >= n:
            break
    return out[:n]


# ---------- list mode (RE) ----------
# Card san pham tren trang category Etsy co dang:
#   - [![alt](img) \\ **TITLE**\\ (294)\\ Sale Price $141.60\\ $236.00\\ (40% off)\\
#     Ad by Etsy seller\\ FREE shipping](https://www.etsy.com/listing/<id>/<slug>?...)
# Parse bang regex => 1 credit/trang, thay vi 5 credits neu dung LLM extraction.
CARD_SPLIT = re.compile(r"\n\s*-\s+\[!\[")
LINK_RE = re.compile(r"\]\((https://www\.etsy\.com/listing/(\d+)/[^?)]+)")
IMG_RE = re.compile(r"(https://i\.etsystatic\.com/[^)\s]+)")

ETSY_SUBCATS = [
    "wall-decor", "vases", "candles-and-holders", "mirrors", "throw-pillows",
    "picture-frames-and-displays", "clocks", "ornaments-and-accents", "indoor-planters",
    "decorative-trays", "home-accents", "seasonal-decor", "wreaths-and-door-hangers",
    "decorative-storage", "faux-plants-and-greenery", "mobiles", "floral-arrangements",
    "chair-pads", "home-fragrances", "fireplace-screens",
]
CAT_BASE = "https://www.etsy.com/c/home-and-living/home-decor"


def _num(m):
    return float(m.group(1).replace(",", "")) if m else None


def parse_cards(md, source):
    """Tach markdown thanh tung card roi boc field. Tra ve list dict."""
    out, seen = [], set()
    for blk in CARD_SPLIT.split(md)[1:]:
        link = LINK_RE.search(blk)
        if not link or link.group(2) in seen:
            continue
        body = blk[:link.start()]          # chi phan truoc URL moi thuoc ve card nay
        title = re.search(r"\*\*(.+?)\*\*", body, re.S)
        if not title:
            continue
        seen.add(link.group(2))
        img = IMG_RE.search(body)
        rev = re.search(r"\((\d[\d,]*)\)", body)
        out.append({
            "site": "etsy", "id": link.group(2), "url": link.group(1),
            "title": " ".join(title.group(1).split()),
            "price": _num(re.search(r"Sale Price \$([\d,]+(?:\.\d+)?)", body)) or _num(re.search(r"\$([\d,]+(?:\.\d+)?)", body)),
            "currency": "USD" if "$" in body else None,
            "original_price": _num(re.search(r"Original Price \$([\d,]+(?:\.\d+)?)", body)),
            "discount_percent": _num(re.search(r"\((\d+)% off\)", body)),
            "review_count": int(rev.group(1).replace(",", "")) if rev else None,
            "is_ad": "Ad by Etsy seller" in body,
            "free_shipping": "FREE shipping" in body,
            "is_bestseller": ("Bestseller" in body) or ("Star Seller" in body),
            "image_url": img.group(1) if img else None,
            "source": source,
        })
    return out


def list_pages(targets, pages=1):
    """Scrape trang category (1 credit/trang) va parse card bang regex."""
    jobs = []
    for t in targets:
        base = t if t.startswith("http") else f"{CAT_BASE}/{t}"
        for pg in range(1, pages + 1):
            sep = "&" if "?" in base else "?"
            jobs.append((f"{base}{sep}page={pg}", t))

    def one(job):
        url, src = job
        res = scrape(url, ["markdown"])
        if not res.get("success"):
            print(f"  ! {src}: {res.get('error')}", file=sys.stderr)
            return []
        rows = parse_cards(res["data"].get("markdown") or "", src)
        if not rows:
            print(f"  ! {src}: 0 card (layout doi?)", file=sys.stderr)
        return rows

    rows, seen = [], set()
    with ThreadPoolExecutor(max_workers=5) as ex:
        for batch in ex.map(one, jobs):
            for r in batch:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                rows.append(r)
    return rows


# ---------- product ----------
CURRENCY = {"$": "USD", "US$": "USD", "USD$": "USD", "£": "GBP", "€": "EUR", "": None}


def product(url):
    site = site_of(url)
    fetch = url.rstrip("/") + "/" if site == "amazon" else url  # /dp/ASIN/ it bi chan hon
    res = scrape(fetch, ["markdown", json_fmt(PRODUCT_SCHEMA, "Trich xuat thong tin san pham tu trang nay.")])
    if not res.get("success"):
        return {"url": url, "site": site, "ok": False, "error": res.get("error")}
    data = res["data"]
    meta = data.get("metadata") or {}
    status = meta.get("statusCode")
    rec = {"url": url, "site": site, "ok": status == 200, "status": status,
           "page_title": meta.get("title"), "markdown_len": len(data.get("markdown") or "")}
    rec.update(data.get("json") or {})
    rec["currency"] = CURRENCY.get((rec.get("currency") or "").strip(), rec.get("currency"))
    if status != 200:
        rec["error"] = f"HTTP {status} (co the bi chan bot hoac listing da go)"
    return rec


def save(rows, name):
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    cols = ["site", "ok", "status", "title", "brand_or_shop", "price", "currency", "original_price",
            "discount_percent", "rating", "review_count", "is_bestseller", "favorites", "materials",
            "dimensions", "ships_from", "availability", "is_ad", "free_shipping", "source",
            "url", "image_url"]
    rows = [dict(r, materials=", ".join(r.get("materials") or [])) for r in rows]
    with (OUT / f"{name}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"-> out/{name}.json  |  out/{name}.csv  ({len(rows)} dong)")


def scrape_all(urls, workers=4):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(product, urls))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover"); d.add_argument("site", choices=["amazon", "etsy"]); d.add_argument("query"); d.add_argument("-n", type=int, default=5); d.add_argument("--pages", type=int, default=1)
    p = sub.add_parser("product"); p.add_argument("urls", nargs="+")
    r = sub.add_parser("run"); r.add_argument("amazon_query"); r.add_argument("etsy_query"); r.add_argument("-n", type=int, default=3); r.add_argument("--pages", type=int, default=1)
    s_ = sub.add_parser("shop"); s_.add_argument("shop_url"); s_.add_argument("-n", type=int, default=8); s_.add_argument("--pages", type=int, default=3)
    l = sub.add_parser("list", help="Re: quet nhieu subcategory bang regex, 1 credit/trang")
    l.add_argument("subcats", nargs="*", help="ten subcat hoac URL; bo trong = dung 10 subcat mac dinh")
    l.add_argument("--pages", type=int, default=1); l.add_argument("--out", default="listing")
    e = sub.add_parser("enrich", help="Doc file list roi scrape chi tiet N san pham (6 credits/sp)")
    e.add_argument("src", help="file JSON tu lenh list"); e.add_argument("-n", type=int, default=20)
    e.add_argument("--out", default="enriched"); e.add_argument("--min-price", type=float, default=0)
    c = sub.add_parser("category", help="Scrape 1 category Etsy, vd /c/home-and-living/home-decor")
    c.add_argument("category_url"); c.add_argument("-n", type=int, default=20); c.add_argument("--pages", type=int, default=2)
    c.add_argument("--out", default="category")

    a = ap.parse_args()
    if a.cmd == "list":
        targets = a.subcats or ETSY_SUBCATS[:10]
        print(f"[list] {len(targets)} subcat x {a.pages} trang = {len(targets)*a.pages} credits")
        rows = list_pages(targets, a.pages)
        save(rows, a.out)
        from collections import Counter
        for src, n in Counter(r["source"] for r in rows).most_common():
            print(f"  {n:>4}  {src}")
        px = [r["price"] for r in rows if r.get("price")]
        if px:
            px.sort()
            print(f"\n  {len(rows)} san pham | gia {px[0]:.0f}-{px[-1]:.0f} USD, trung vi {px[len(px)//2]:.0f}")
    elif a.cmd == "enrich":
        src = json.loads(Path(a.src).read_text())
        pool = [r for r in src if (r.get("price") or 0) >= a.min_price]
        urls = [r["url"] for r in pool[:a.n]]
        print(f"[enrich] {len(urls)} san pham x 6 credits = ~{len(urls)*6} credits")
        rows = scrape_all(urls, workers=6)
        save(rows, a.out)
        print(f"Thanh cong {sum(1 for x in rows if x.get('ok'))}/{len(rows)}")
    elif a.cmd == "discover":
        items = discover(a.site, a.query, a.n, a.pages)
        print(json.dumps(items, indent=2, ensure_ascii=False))
    elif a.cmd == "category":
        urls = shop_listings(a.category_url, a.n, a.pages)
        print(f"[category] gom duoc {len(urls)} listing tu {a.pages} trang")
        rows = scrape_all(urls, workers=6)
        save(rows, a.out)
        ok = sum(1 for x in rows if x.get("ok"))
        print(f"\nThanh cong {ok}/{len(rows)}")
        for x in rows:
            if not x.get("ok"):
                continue
            star = "*" if x.get("is_bestseller") else " "
            print(f" {star} {str(x.get('title'))[:48]:<48} {str(x.get('price')):>7} {x.get('currency') or '':<4} "
                  f"{x.get('rating')}/{x.get('review_count')}  {str(x.get('brand_or_shop'))[:18]}")
    elif a.cmd == "shop":
        urls = shop_listings(a.shop_url, a.n, a.pages)
        print(f"[shop] tim thay {len(urls)} listing")
        rows = scrape_all(urls)
        save(rows, "shop")
        for x in rows:
            print(f"  {'OK ' if x.get('ok') else 'FAIL'} {str(x.get('title'))[:55]:<55} {x.get('price')} {x.get('currency') or ''}")
    elif a.cmd == "product":
        rows = scrape_all(a.urls)
        print(json.dumps(rows, indent=2, ensure_ascii=False)[:4000])
        save(rows, "products")
    else:
        rows = []
        for site, q in (("amazon", a.amazon_query), ("etsy", a.etsy_query)):
            print(f"[discover] {site}: {q!r}")
            found = discover(site, q, a.n, a.pages)
            print(f"  tim thay {len(found)} san pham")
            for it in found:
                print(f"   - {it['id']}  {(it['search_title'] or '')[:70]}")
            print(f"[scrape] {site}: {len(found)} trang chi tiet ...")
            rows += scrape_all([it["url"] for it in found])
        save(rows, "products")
        ok = sum(1 for x in rows if x.get("ok"))
        print(f"\nThanh cong {ok}/{len(rows)}")
        for x in rows:
            mark = "OK " if x.get("ok") else "FAIL"
            print(f"  {mark} [{x['site']}] {str(x.get('title'))[:60]:<60} {x.get('price')} {x.get('currency') or ''} "
                  f"rating={x.get('rating')} n={x.get('review_count')}")


if __name__ == "__main__":
    main()
