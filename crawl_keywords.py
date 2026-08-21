#!/usr/bin/env python3
"""Crawl N trang dau cho moi keyword tren ca Etsy va Amazon.

  python3 crawl_keywords.py sanpham.txt --pages 3
  python3 crawl_keywords.py sanpham.txt --pages 3 --sites etsy      # chi 1 san
  python3 crawl_keywords.py sanpham.txt --dry-run                   # chi uoc tinh credit

Co checkpoint: moi trang xong ghi ra out/raw/. Chay lai se bo qua trang da co.
"""
import argparse, csv, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "out" / "raw"
MD = ROOT / "out" / "md"      # cache markdown tho
API = "https://api.firecrawl.dev/v2"


def api_key():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("FIRECRAWL_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("Thieu FIRECRAWL_API_KEY trong .env")


HEADERS = {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}

# Etsy: proxy basic la du. KHONG dung actions/location -- da test, chung lam
#       trang render hong (gia khong kip load, markdown mat het dau $).
# Amazon: proxy basic cung qua duoc trang /s?k= (chi trang /dp/ moi can stealth),
#       nhung PHAI ep location US, khong thi tra gia GBP.
SITE_OPTS = {
    "etsy": {"proxy": "basic", "waitFor": 4000},
    "amazon": {"proxy": "basic", "waitFor": 4000,
               "location": {"country": "US", "languages": ["en-US"]}},
}
SEARCH_URL = {
    "etsy": lambda q, pg: f"https://www.etsy.com/search?q={requests.utils.quote(q)}&page={pg}",
    "amazon": lambda q, pg: f"https://www.amazon.com/s?k={requests.utils.quote(q)}&page={pg}",
}


def scrape(site, url, parse=None, kw="", page=1):
    """Scrape + parse ngay tai cho.

    Etsy/Amazon co the tra HTTP 200 voi trang render nua chung (gia chua load xong),
    luc do do dai markdown van lon nen khong the dua vao len(md) de biet that bai.
    => Tieu chi that bai la PARSE RA 0 ROW. Moi lan retry tang waitFor, cuoi cung
    moi nang len stealth (dat gap 5 lan).
    """
    ladder = [
        dict(SITE_OPTS[site], waitFor=4000),
        dict(SITE_OPTS[site], waitFor=9000),
        dict(SITE_OPTS[site], waitFor=14000),
        dict(SITE_OPTS[site], waitFor=9000, proxy="stealth"),
    ]
    for opts_extra in ladder:
        opts = dict(opts_extra, url=url, formats=["markdown"],
                    onlyMainContent=True, timeout=120000, blockAds=True)
        try:
            r = requests.post(f"{API}/scrape", headers=HEADERS, json=opts, timeout=200)
            b = r.json()
            if b.get("success"):
                data = b.get("data") or {}
                md = data.get("markdown") or ""
                code = (data.get("metadata") or {}).get("statusCode")
                if code == 200 and md:
                    rows = parse(md, kw, page) if parse else []
                    if rows or not parse:
                        return md, rows, opts["proxy"], opts["waitFor"]
        except Exception:
            pass
        time.sleep(2)
    return None, [], opts["proxy"], opts["waitFor"]


# ---------- parsers ----------
def _k(s):
    s = s.replace("+", "").replace(",", "").strip().upper()
    return int(float(s[:-1]) * 1000) if s.endswith("K") else int(float(s))


def _f(m, g=1):
    return float(m.group(g).replace(",", "")) if m else None


def parse_etsy(md, kw, page):
    """Card Etsy search: [**TITLE**\\ 4.8(5.9k)\\ Made by **Shop**\\ 3 years on Etsy\\ Sale Price $6.55 ...](url)"""
    out, seen = [], set()
    parts = re.split(r"\[\*\*", md)      # KHONG rang buoc \n: Amazon chen "Sponsored" dinh lien truoc [**
    for i, blk in enumerate(parts[1:], start=1):
        ctx = parts[i - 1][-900:] + blk[:900]      # badge nam o block anh phia truoc
        u = re.search(r"\]\((https://www\.etsy\.com/listing/(\d+)/[^?)]+)", blk)
        if not u or u.group(2) in seen:
            continue
        body, t = blk[:u.start()], re.match(r"(.+?)\*\*", blk, re.S)
        if not t:
            continue
        seen.add(u.group(2))
        rr = re.search(r"(\d\.\d)\(([\d.,]+k?)\)", body, re.I)
        shop = re.search(r"Made by \*\*(.+?)\*\*", body)
        age = re.search(r"([\d.]+) years? on Etsy", body)
        out.append({
            "site": "etsy", "id": u.group(2), "url": u.group(1), "keyword": kw, "page": page,
            "title": " ".join(t.group(1).split()),
            "price": _f(re.search(r"Sale Price \$([\d,]+(?:\.\d+)?)", body)) or _f(re.search(r"\$([\d,]+(?:\.\d+)?)", body)),
            "original_price": _f(re.search(r"Original Price \$([\d,]+(?:\.\d+)?)", body)),
            "discount_percent": _f(re.search(r"\((\d+)% off\)", body)),
            "rating": float(rr.group(1)) if rr else None,
            "review_count": _k(rr.group(2)) if rr else None,
            "shop": shop.group(1) if shop else None,
            "shop_age_years": float(age.group(1)) if age else None,
            "is_ad": ("Ad from shop" in ctx) or ("Ad **・**" in ctx),
            "is_bestseller": "Bestseller" in ctx,
            "is_star_seller": "Star Seller" in ctx,
            "is_popular_now": "Popular now" in ctx,
            "is_etsy_pick": "Etsy's Pick" in ctx or "Etsy\u2019s Pick" in ctx,
            "free_shipping": "FREE shipping" in ctx,
        })
    return out


def parse_amazon(md, kw, page):
    """Card Amazon search: [**TITLE**](url) 4.5_.. out of 5 stars_ [(8)](url) 900+ bought in past month Price, product page [$5.99..."""
    out, seen = [], set()
    parts = re.split(r"\[\*\*", md)      # xem ghi chu o parse_etsy
    for i, blk in enumerate(parts[1:], start=1):
        ctx = parts[i - 1][-900:] + blk[:900]
        prefix = parts[i - 1][-140:]           # badge quang cao nam sat ngay truoc tieu de
        a = re.search(r"/dp/([A-Z0-9]{10})", blk)
        t = re.match(r"(.+?)\*\*\]", blk, re.S)
        if not a or not t or a.group(1) in seen:
            continue
        seen.add(a.group(1))
        rat = re.search(r"([\d.]+)_[\d.]+ out of 5 stars_", blk)
        rev = re.search(r"\[\((\d[\d,]*)\)\]", blk)
        bought = re.search(r"([\d.]+K?\+?) bought in past month", blk, re.I)
        out.append({
            "site": "amazon", "id": a.group(1), "keyword": kw, "page": page,
            "url": f"https://www.amazon.com/dp/{a.group(1)}",
            "title": " ".join(t.group(1).replace("\\", "").split()),
            "price": _f(re.search(r"Price, product page \[\$([\d,]+(?:\.\d+)?)", blk)),
            "original_price": _f(re.search(r"List: \$([\d,]+(?:\.\d+)?)", blk)),
            "rating": float(rat.group(1)) if rat else None,
            "review_count": int(rev.group(1).replace(",", "")) if rev else None,
            "bought_past_month": _k(bought.group(1)) if bought else None,
            "is_new": "New on Amazon in past month" in blk,   # tin hieu xu huong moi noi
            "is_ad": "Sponsored" in prefix.replace("sponsored-ads", ""),
            "is_bestseller": "Best Seller" in ctx,
            "is_amazon_choice": "Amazon's Choice" in ctx,
            "is_overall_pick": "Overall Pick" in ctx,
        })
    return out


PARSER = {"etsy": parse_etsy, "amazon": parse_amazon}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword_file")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--sites", default="etsy,amazon")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retry-empty", action="store_true", help="xoa checkpoint 0 row roi crawl lai")
    ap.add_argument("--out", default="market")
    a = ap.parse_args()

    kws = [l.strip() for l in Path(a.keyword_file).read_text().splitlines() if l.strip()]
    sites = a.sites.split(",")
    jobs = [(s, kw, pg) for kw in kws for pg in range(1, a.pages + 1) for s in sites]
    RAW.mkdir(parents=True, exist_ok=True)
    MD.mkdir(parents=True, exist_ok=True)
    if a.retry_empty:
        wiped = 0
        for f in RAW.glob("*.json"):
            try:
                if json.loads(f.read_text()).get("n", 0) == 0:
                    f.unlink(); wiped += 1
            except Exception:
                f.unlink(); wiped += 1
        print(f"  xoa {wiped} checkpoint rong de crawl lai")
    todo = [j for j in jobs if not (RAW / f"{j[0]}_{slug(j[1])}_p{j[2]}.json").exists()]

    print(f"{len(kws)} keyword x {a.pages} trang x {len(sites)} san = {len(jobs)} luot scrape")
    print(f"  da co checkpoint: {len(jobs)-len(todo)} | can chay: {len(todo)}")
    print(f"  uoc tinh: ~{len(todo)} credits (toi da ~{len(todo)*5} neu tat ca phai dung stealth)")
    if a.dry_run:
        return

    done, lock = [0], Lock()
    t0 = time.time()

    def work(job):
        site, kw, pg = job
        md, rows, used, waited = scrape(site, SEARCH_URL[site](kw, pg), PARSER[site], kw, pg)
        if md:                                   # cache markdown -> lan sau doi parser khong ton credit
            (MD / f"{site}_{slug(kw)}_p{pg}.md").write_text(md)
        (RAW / f"{site}_{slug(kw)}_p{pg}.json").write_text(
            json.dumps({"keyword": kw, "page": pg, "site": site, "proxy": used,
                        "waitFor": waited, "n": len(rows), "rows": rows}, ensure_ascii=False))
        with lock:
            done[0] += 1
            el = time.time() - t0
            eta = el / done[0] * (len(todo) - done[0])
            flag = "" if rows else "  <-- 0 row"
            print(f"[{done[0]:>3}/{len(todo)}] {site:<6} p{pg} {kw[:34]:<34} {len(rows):>3} sp "
                  f"({used}/{waited}ms) eta {eta/60:.0f}m{flag}", flush=True)
        return rows

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for f in as_completed([ex.submit(work, j) for j in todo]):
            f.result()

    # gop tat ca checkpoint lai
    allrows = []
    for f in sorted(RAW.glob("*.json")):
        allrows += json.loads(f.read_text()).get("rows", [])
    uniq, seen = [], set()
    for r in allrows:
        k = (r["site"], r["id"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    out = ROOT / "out"
    (out / f"{a.out}_raw.json").write_text(json.dumps(allrows, ensure_ascii=False))
    (out / f"{a.out}.json").write_text(json.dumps(uniq, indent=2, ensure_ascii=False))
    cols = ["site", "id", "keyword", "page", "title", "price", "original_price", "discount_percent",
            "rating", "review_count", "bought_past_month", "is_new", "shop", "shop_age_years",
            "is_ad", "is_bestseller", "is_star_seller", "is_popular_now", "is_etsy_pick",
            "is_amazon_choice", "is_overall_pick", "free_shipping", "url"]
    with (out / f"{a.out}.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(uniq)
    print(f"\nTHO {len(allrows)} dong -> DEDUP {len(uniq)} san pham")
    for s in sites:
        print(f"  {s}: {sum(1 for r in uniq if r['site']==s)}")
    print(f"-> out/{a.out}.csv | out/{a.out}.json | out/{a.out}_raw.json (giu keyword lap)")


if __name__ == "__main__":
    main()
