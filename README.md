# cross-border-ai-pw1 — Market data crawler (Etsy + Amazon)

Crawl dữ liệu sản phẩm từ **Etsy** và **Amazon** bằng [Firecrawl](https://firecrawl.dev),
làm đầu vào cho bài toán Opportunity Score / R&D hub (xem `yeucau.txt`).

## Dataset

`out/market.csv` — **11.845 sản phẩm** (6.181 Etsy + 5.664 Amazon),
crawl từ 60 keyword × 3 trang đầu × 2 sàn (`sanpham.txt`, chủ đề Christmas).
Chi phí: **354 Firecrawl credits**.

| Cột | Etsy | Amazon | Ghi chú |
|---|---|---|---|
| `price` | 6.164 (100%) | 5.241 (93%) | USD |
| `rating` | 2.405 (39%) | 5.382 (95%) | Etsy chỉ hiện sao khi listing đã có review |
| `review_count` | 2.405 | 3.702 | |
| `original_price`, `discount_percent` | 58% | 16% | giá gốc trước sale |
| `shop`, `shop_age_years` | 41% / 5.269 (85%) | — | tuổi shop dùng cho chiều cạnh tranh |
| `bought_past_month` | — | 2.149 (38%) | tín hiệu velocity 30 ngày |
| `is_new` | — | 226 | `New on Amazon in past month` — xu hướng mới nổi |
| `is_ad` | 1.460 (24%) | xem hạn chế | lọc ra trước khi phân tích giá |

Các file CSV khác: `out/home_decor_list.csv` (637 sp, 10 subcategory Etsy home decor),
`out/home_decor.csv` (12 sp có đủ materials/dimensions/favorites/ships_from).

Dữ liệu thô (`out/raw/`, `out/md/`, `*.json`) **không** đẩy lên repo — xem `.gitignore`.

## Chạy

```bash
cp .env.example .env        # điền FIRECRAWL_API_KEY
pip install requests

python3 crawl_keywords.py sanpham.txt --pages 3 --dry-run   # ước tính credit
python3 crawl_keywords.py sanpham.txt --pages 3             # crawl thật
python3 crawl_keywords.py sanpham.txt --pages 3 --retry-empty   # vá trang lỗi
```

Có checkpoint theo từng trang trong `out/raw/`, chạy lại sẽ bỏ qua trang đã xong.
Markdown thô được cache ở `out/md/` để đổi parser mà không tốn credit.

`firecrawl_shop.py` là công cụ phụ: quét theo category/shop Etsy, scrape trang chi tiết,
và scrape sản phẩm Amazon theo URL.

```bash
python3 firecrawl_shop.py list --pages 5                  # quét subcategory home decor
python3 firecrawl_shop.py enrich out/home_decor_list.json -n 100
python3 firecrawl_shop.py product <url> ...
```

## Ghi chú kỹ thuật (đã đo, không phải phỏng đoán)

- **Amazon trang `/s?k=` chỉ cần `proxy: basic`** (1 credit). Chỉ trang `/dp/` mới cần
  `stealth` (5 credits) — không có nó Amazon trả về trang 404 "Dogs of Amazon".
- **Amazon phải ép `location: {country: "US"}`**, nếu không giá trả về là GBP.
- **Etsy không cần stealth.** Tuyệt đối không dùng `actions` (scroll/wait) cho Etsy —
  đã test, chúng làm trang render hỏng và markdown mất sạch dấu `$`.
- Etsy/Amazon hay trả HTTP 200 với trang **render nửa chừng**, độ dài markdown vẫn lớn
  nên không thể dùng `len(md)` để phát hiện. Tiêu chí thất bại là **parse ra 0 row**;
  `scrape()` retry theo thang `waitFor` 4s → 9s → 14s → stealth. Thang này kéo tỉ lệ
  trang lỗi từ 31% xuống 0.3%.
- Trang danh sách parse bằng **regex, không dùng LLM extraction**: 1 credit/trang thay vì
  5, và cho 48-64 sản phẩm/trang thay vì ~10.

## Hạn chế đã biết

- `is_ad` phía Amazon trong `out/market.csv` hiện **không đáng tin** (parser cũ bỏ sót
  marker `Sponsored` dính liền tiêu đề). Parser đã sửa trong `crawl_keywords.py`
  nhưng dataset chưa crawl lại. Cùng lý do, thiếu ~4% sản phẩm Amazon và các badge
  `Bestseller` / `Popular now` / `Amazon's Choice` / `Overall Pick`.
- 1/360 trang vẫn trả 0 row sau 3 lượt retry (`etsy p3 christmas decorations ideas`).
- Chưa có time-series: mọi số đều là snapshot ngày 2026-08-21. "Tăng trưởng" chỉ suy ra
  được từ proxy `bought_past_month` / `is_new`, không phải đo thật.
