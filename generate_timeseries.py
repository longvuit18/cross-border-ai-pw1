#!/usr/bin/env python3
"""
generate_timeseries.py
----------------------
Mô phỏng chuỗi thời gian (time-series) 30 ngày cho tập dữ liệu crawled `out/market.csv`.

Các quy luật mô phỏng:
1. Điểm neo (Anchor Date): 2026-08-21 (T=29, ngày crawl hiện tại).
2. Thời gian: 30 ngày từ 2026-07-23 (T=0) đến 2026-08-21 (T=29).
3. Phân loại Archetype hành vi sản phẩm:
   - Emerging / Viral Stars (15%): Tăng trưởng mạnh gần đây, thứ hạng cải thiện.
   - Stable Bestsellers (25%): Ổn định, volume cao, duy trì Page 1.
   - Seasonal Q4 Ramp-up (40%): Tăng dần theo hàm S-curve đón mùa Giáng sinh.
   - Stagnant / Low Momentum (20%): Đi ngang hoặc giảm nhẹ.
4. Tính nhất quán:
   - Review count tăng lũy tiến đơn điệu theo thời gian: review(t-1) <= review(t).
   - Chu kỳ mua sắm cuối tuần (Weekend spike +15% - 25%).
   - Flash sale / giảm giá theo chu kỳ ngắn hạn (2-4 ngày).
   - Tự động sinh `estimated_daily_sales`, `estimated_daily_revenue`, `daily_rank`.
"""

import argparse
import hashlib
import math
import os
import sys
from datetime import datetime, timedelta
import numpy as np
import pandas as pd


def get_deterministic_seed(item_id: str, salt: int = 0) -> int:
    """Tạo seed ngẫu nhiên cố định từ ID sản phẩm để đảm bảo tính tái lập 100%."""
    h = hashlib.md5(f"{item_id}_{salt}".encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def generate_timeseries(
    input_path: str,
    output_dir: str,
    days: int = 30,
    end_date_str: str = "2026-08-21",
    seed: int = 42,
    export_csv: bool = True,
    export_parquet: bool = True,
    export_keyword_agg: bool = True,
):
    print(f"[*] Đang tải dữ liệu từ: {input_path}")
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Không tìm thấy file: {input_path}")

    df_base = pd.read_csv(input_path)
    total_products = len(df_base)
    print(f"[+] Đã tải {total_products:,} sản phẩm ({df_base['site'].value_counts().to_dict()})")

    # Thiết lập ngày
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
    date_list = [
        (end_date - timedelta(days=(days - 1 - i))).strftime("%Y-%m-%d")
        for i in range(days)
    ]
    # Day-of-week multipliers: Mon=0 -> Sun=6
    # Thứ 7 và CN tăng 20-25%
    dow_multipliers = {
        0: 0.93,  # Thứ 2
        1: 0.95,  # Thứ 3
        2: 0.98,  # Thứ 4
        3: 1.00,  # Thứ 5
        4: 1.08,  # Thứ 6
        5: 1.22,  # Thứ 7
        6: 1.25,  # Chủ nhật
    }

    # Phân loại Archetype cho từng sản phẩm
    print("[*] Đang phân loại Archetype hành vi và khởi tạo thông số mô phỏng...")
    np.random.seed(seed)

    records = df_base.to_dict(orient="records")
    all_daily_rows = []

    for prod_idx, row in enumerate(records):
        item_id = str(row["id"])
        site = row["site"]
        keyword = row["keyword"]
        is_new_flag = bool(row.get("is_new") is True or str(row.get("is_new")).lower() == "true")
        curr_price = float(row["price"]) if pd.notnull(row.get("price")) else None
        curr_orig_price = float(row["original_price"]) if pd.notnull(row.get("original_price")) else None
        curr_discount = float(row["discount_percent"]) if pd.notnull(row.get("discount_percent")) else None
        curr_rating = float(row["rating"]) if pd.notnull(row.get("rating")) else None
        curr_reviews = float(row["review_count"]) if pd.notnull(row.get("review_count")) else None
        curr_bpm = float(row["bought_past_month"]) if pd.notnull(row.get("bought_past_month")) else None
        curr_bestseller = bool(row.get("is_bestseller") is True or str(row.get("is_bestseller")).lower() == "true")
        curr_ad = bool(row.get("is_ad") is True or str(row.get("is_ad")).lower() == "true")
        shop_age = float(row["shop_age_years"]) if pd.notnull(row.get("shop_age_years")) else None
        curr_page = int(row.get("page", 1))

        # Seed riêng cho từng sản phẩm
        prod_seed = get_deterministic_seed(item_id, seed)
        rng = np.random.RandomState(prod_seed)

        # Quyết định Archetype
        rand_val = rng.uniform(0, 100)
        if is_new_flag:
            archetype = "emerging"
            entry_day = rng.randint(5, 18)  # Sản phẩm mới xuất hiện từ giữa tháng
        elif curr_bpm and curr_bpm >= 2000 or (curr_reviews and curr_reviews >= 2000):
            archetype = "bestseller" if rand_val < 60 else "seasonal"
            entry_day = 0
        elif curr_bestseller:
            archetype = "bestseller"
            entry_day = 0
        else:
            if rand_val < 18:
                archetype = "emerging"
                entry_day = rng.randint(0, 8)
            elif rand_val < 50:
                archetype = "seasonal"
                entry_day = 0
            elif rand_val < 80:
                archetype = "bestseller"
                entry_day = 0
            else:
                archetype = "stagnant"
                entry_day = 0

        # Ước lượng baseline daily sales tại ngày cuối T=29
        if curr_bpm is not None and curr_bpm > 0:
            end_daily_sales = curr_bpm / 30.0
        elif curr_reviews is not None and curr_reviews > 0:
            if curr_reviews > 5000:
                end_daily_sales = rng.uniform(30, 80)
            elif curr_reviews > 1000:
                end_daily_sales = rng.uniform(12, 35)
            elif curr_reviews > 200:
                end_daily_sales = rng.uniform(4, 15)
            else:
                end_daily_sales = rng.uniform(0.5, 4)
        else:
            end_daily_sales = rng.uniform(0.1, 2.0)

        # Mô phỏng Flash sale event (10% sản phẩm có 1 đợt sale ngắn)
        has_flash_sale = rng.uniform(0, 1) < 0.12
        flash_sale_start = rng.randint(6, 22) if has_flash_sale else -1
        flash_sale_duration = rng.randint(2, 5) if has_flash_sale else 0
        flash_discount_rate = rng.uniform(0.15, 0.30) if has_flash_sale else 0.0

        # Mảng sinh dữ liệu 30 ngày
        daily_sales_series = np.zeros(days)
        daily_price_series = np.zeros(days)
        daily_orig_price_series = [None] * days
        daily_discount_series = [None] * days
        daily_rating_series = [None] * days
        daily_reviews_series = [None] * days
        daily_bpm_series = [None] * days
        daily_is_new_series = [False] * days
        daily_is_bestseller_series = [False] * days
        daily_is_ad_series = [False] * days

        # 1. Tính toán đường cong daily sales G(t) từ t=0 đến 29
        for t in range(days):
            if t < entry_day:
                daily_sales_series[t] = 0.0
                continue

            u = t / float(days - 1)  # u từ 0.0 đến 1.0
            dt = datetime.strptime(date_list[t], "%Y-%m-%d")
            dow_mult = dow_multipliers[dt.weekday()]
            noise = rng.normal(1.0, 0.06)  # 6% noise

            if archetype == "emerging":
                # Tăng tốc mạnh trong những ngày gần đây
                curve = 0.12 + 0.88 * math.pow(u, 2.3)
            elif archetype == "bestseller":
                # Ổn định, dao động nhẹ
                curve = 0.88 + 0.12 * u
            elif archetype == "seasonal":
                # S-curve ramp up đón Q4
                curve = 0.35 + 0.65 / (1.0 + math.exp(-6.0 * (u - 0.45)))
            else:  # stagnant
                # Giảm nhẹ hoặc đi ngang
                curve = 1.08 - 0.08 * u

            sales_val = end_daily_sales * curve * dow_mult * noise
            # Flash sale boost
            if has_flash_sale and flash_sale_start <= t < flash_sale_start + flash_sale_duration:
                sales_val *= 1.45

            daily_sales_series[t] = max(0.0, sales_val)

        # 2. Sinh Review Count: Đi ngược từ t=28 về 0 để đảm bảo tính đơn điệu
        if curr_reviews is not None:
            daily_reviews_series[days - 1] = int(round(curr_reviews))
            # Xác suất để mỗi lượt mua để lại 1 review ~ 2.0% - 3.5%
            review_conversion = rng.uniform(0.02, 0.035)

            for t in range(days - 2, -1, -1):
                if t < entry_day:
                    daily_reviews_series[t] = 0
                else:
                    # Số review mới sinh ra trong ngày t+1
                    expected_new_rev = daily_sales_series[t + 1] * review_conversion
                    new_rev = rng.poisson(max(0.01, expected_new_rev))
                    # Lùi lại: review ngày t = review ngày t+1 trừ đi new_rev
                    prev_rev = max(0, daily_reviews_series[t + 1] - new_rev)
                    daily_reviews_series[t] = prev_rev

        # 3. Sinh Giá & Khuyến mãi
        for t in range(days):
            if curr_price is None:
                daily_price_series[t] = None
                continue

            if has_flash_sale and flash_sale_start <= t < flash_sale_start + flash_sale_duration:
                # Giá trong flash sale
                p = round(curr_price * (1.0 - flash_discount_rate), 2)
                orig_p = curr_orig_price if curr_orig_price else curr_price
                disc = round((1.0 - p / orig_p) * 100.0, 1)
            else:
                p = curr_price
                orig_p = curr_orig_price
                disc = curr_discount

            daily_price_series[t] = p
            daily_orig_price_series[t] = orig_p
            daily_discount_series[t] = disc

        # 4. Sinh Rating
        for t in range(days):
            if curr_rating is None or (curr_reviews is not None and daily_reviews_series[t] == 0):
                daily_rating_series[t] = None
            else:
                # Rating vi biến thiên rất nhỏ quanh mốc hiện tại (+- 0.05)
                delta = rng.normal(0, 0.03)
                r = min(5.0, max(1.0, round(curr_rating + delta, 1)))
                daily_rating_series[t] = r

        # 5. Sinh Bought Past Month (rolling 30-day velocity cho Amazon)
        for t in range(days):
            if site == "amazon":
                if t < entry_day:
                    daily_bpm_series[t] = None
                elif curr_bpm is not None:
                    u = t / float(days - 1)
                    if archetype == "emerging":
                        bpm_curve = 0.15 + 0.85 * math.pow(u, 2.0)
                    elif archetype == "seasonal":
                        bpm_curve = 0.45 + 0.55 / (1.0 + math.exp(-5.0 * (u - 0.5)))
                    elif archetype == "bestseller":
                        bpm_curve = 0.90 + 0.10 * u
                    else:
                        bpm_curve = 1.05 - 0.05 * u

                    val = curr_bpm * bpm_curve
                    # Làm tròn theo các mốc Amazon thường hiển thị
                    if val < 50:
                        rounded_bpm = 50.0 if val >= 25 else 0.0
                    elif val < 1000:
                        rounded_bpm = round(val / 50.0) * 50.0
                    else:
                        rounded_bpm = round(val / 100.0) * 100.0
                    daily_bpm_series[t] = float(rounded_bpm) if rounded_bpm > 0 else None
                else:
                    daily_bpm_series[t] = None

        # 6. Badges & Ads
        for t in range(days):
            if is_new_flag and t >= entry_day and (days - 1 - t) <= 30:
                daily_is_new_series[t] = True
            else:
                daily_is_new_series[t] = False

            # Bestseller badge
            if archetype == "bestseller" and daily_sales_series[t] >= 15:
                daily_is_bestseller_series[t] = True
            elif t == (days - 1):
                daily_is_bestseller_series[t] = curr_bestseller
            else:
                daily_is_bestseller_series[t] = False

            # Is ad (quảng cáo bật/tắt)
            if curr_ad:
                daily_is_ad_series[t] = rng.uniform(0, 1) < 0.85
            else:
                daily_is_ad_series[t] = rng.uniform(0, 1) < 0.05

        # Gom các ngày vào bảng kết quả
        for t in range(days):
            dt_str = date_list[t]
            p_val = daily_price_series[t]
            sales_val = round(daily_sales_series[t], 1)
            rev_val = round(sales_val * (p_val if p_val else 0.0), 2)

            # Score dùng để xếp hạng rank trong ngày
            rank_score = (
                sales_val * 2.0
                + (daily_reviews_series[t] if daily_reviews_series[t] else 0) * 0.01
                + (50.0 if daily_is_bestseller_series[t] else 0.0)
                + (30.0 if daily_is_ad_series[t] else 0.0)
                + rng.normal(0, 2.0)
            )

            row_data = {
                "date": dt_str,
                "day_index": t,
                "site": site,
                "id": item_id,
                "keyword": keyword,
                "title": row["title"],
                "price": p_val,
                "original_price": daily_orig_price_series[t],
                "discount_percent": daily_discount_series[t],
                "rating": daily_rating_series[t],
                "review_count": daily_reviews_series[t],
                "bought_past_month": daily_bpm_series[t],
                "estimated_daily_sales": sales_val,
                "estimated_daily_revenue": rev_val,
                "archetype": archetype,
                "is_new": daily_is_new_series[t],
                "shop": row.get("shop"),
                "shop_age_years": shop_age,
                "is_ad": daily_is_ad_series[t],
                "is_bestseller": daily_is_bestseller_series[t],
                "free_shipping": row.get("free_shipping"),
                "url": row.get("url"),
                "_rank_score": rank_score,
                "_original_page": curr_page,
            }
            all_daily_rows.append(row_data)

    print(f"[+] Đã sinh xong {len(all_daily_rows):,} dòng time-series (30 ngày x {total_products:,} sản phẩm).")

    # Chuyển đổi thành DataFrame để gán Rank và Page theo từng ngày & keyword
    print("[*] Đang tính toán Rank và Page động cho từng ngày...")
    df_ts = pd.DataFrame(all_daily_rows)

    # Sắp xếp và đánh rank theo từng (date, keyword, site)
    df_ts["rank_position"] = df_ts.groupby(["date", "keyword", "site"])["_rank_score"].rank(
        ascending=False, method="first"
    ).astype(int)

    # Page tương ứng: ~48 items mỗi page
    df_ts["page"] = ((df_ts["rank_position"] - 1) // 48 + 1).clip(upper=3)

    # Xóa cột phụ trợ
    df_ts.drop(columns=["_rank_score", "_original_page"], inplace=True)

    # Sắp xếp chuẩn theo date, keyword, rank
    df_ts.sort_values(by=["date", "keyword", "site", "rank_position"], inplace=True)

    # Tạo thư mục output nếu chưa có
    os.makedirs(output_dir, exist_ok=True)

    # 1. Xuất file chi tiết
    if export_csv:
        csv_out_path = os.path.join(output_dir, "market_timeseries_30d.csv")
        print(f"[*] Đang ghi file CSV chi tiết: {csv_out_path} ...")
        df_ts.to_csv(csv_out_path, index=False)
        print(f"[✓] Đã xuất CSV: {csv_out_path} ({os.path.getsize(csv_out_path) / (1024*1024):.1f} MB)")

    if export_parquet:
        parquet_out_path = os.path.join(output_dir, "market_timeseries_30d.parquet")
        print(f"[*] Đang ghi file Parquet tối ưu: {parquet_out_path} ...")
        df_ts.to_parquet(parquet_out_path, index=False, engine="pyarrow", compression="snappy")
        print(f"[✓] Đã xuất Parquet: {parquet_out_path} ({os.path.getsize(parquet_out_path) / (1024*1024):.1f} MB)")

    # 2. Xuất file tổng hợp theo Keyword qua 30 ngày (Keyword Trends Aggregation)
    if export_keyword_agg:
        print("[*] Đang tạo bảng tổng hợp xu hướng Keyword 30 ngày...")
        kw_agg = df_ts.groupby(["date", "keyword"]).agg(
            total_listings=("id", "count"),
            avg_price=("price", "mean"),
            median_price=("price", "median"),
            total_daily_sales=("estimated_daily_sales", "sum"),
            total_daily_revenue=("estimated_daily_revenue", "sum"),
            avg_rating=("rating", "mean"),
            total_reviews=("review_count", "sum"),
            bestseller_count=("is_bestseller", "sum"),
            ad_count=("is_ad", "sum"),
            new_items_count=("is_new", "sum"),
        ).reset_index()

        # Tính Search Volume Index mô phỏng chuẩn hóa (10-100) dựa trên total_daily_sales
        max_sales_per_kw = kw_agg.groupby("keyword")["total_daily_sales"].transform("max")
        kw_agg["search_volume_index"] = (
            (kw_agg["total_daily_sales"] / (max_sales_per_kw + 1e-5)) * 90.0 + 10.0
        ).round(1)

        kw_csv_path = os.path.join(output_dir, "keyword_trends_30d.csv")
        kw_agg.to_csv(kw_csv_path, index=False)
        print(f"[✓] Đã xuất Keyword Trends: {kw_csv_path}")

    print("\n" + "=" * 60)
    print("HOÀN TẤT SINH DỮ LIỆU TIME-SERIES 30 NGÀY!")
    print(f"• Tổng số dòng: {len(df_ts):,}")
    print(f"• Khoảng thời gian: {date_list[0]} -> {date_list[-1]} ({days} ngày)")
    print(f"• Số lượng keyword: {df_ts['keyword'].nunique()}")
    print("=" * 60)

    return df_ts


def main():
    parser = argparse.ArgumentParser(description="Mô phỏng chuỗi thời gian 30 ngày cho market.csv")
    parser.add_argument(
        "--input",
        "-i",
        default="/Users/dauquocduy/workspace/HKT/out/market.csv",
        help="Đường dẫn file market.csv gốc",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="/Users/dauquocduy/workspace/HKT/out",
        help="Thư mục xuất dữ liệu đầu ra",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=30,
        help="Số ngày chuỗi thời gian (mặc định: 30)",
    )
    parser.add_argument(
        "--end-date",
        default="2026-08-21",
        help="Ngày neo cuối cùng YYYY-MM-DD (mặc định: 2026-08-21)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed đảm bảo tính tái lập (mặc định: 42)",
    )
    args = parser.parse_args()

    generate_timeseries(
        input_path=args.input,
        output_dir=args.output_dir,
        days=args.days,
        end_date_str=args.end_date,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
