from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st

from backend.crawler.etsy_detail_worker import canonical_listing_urls
from backend.normalization import build_normalized_document, load_json
from backend.workers import CrawlWorker, WorkerRepository
from backend.domain.firecrawl_models import firecrawl_location_payload

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PRINTWAY_PATH = DATA_DIR / "printway_catalog.json"
WORKERS_ROOT = DATA_DIR / "workers"
REPOSITORY = WorkerRepository(WORKERS_ROOT, printway_path=PRINTWAY_PATH)

st.set_page_config(
    page_title="HKT Worker Dashboard",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background: #f7f7f5; }
    [data-testid="stSidebar"] { background: #101418; }
    [data-testid="stSidebar"] * { color: #f4f4ef; }
    [data-testid="stSidebar"] input { color: #17231e !important; }
    [data-testid="stSidebar"] [data-baseweb="select"] * { color: #17231e !important; }
    [data-testid="stSidebar"] code { color:#dce9e3 !important; background:#26322d !important; }
    .hero { padding:1.35rem 1.55rem; border-radius:22px; background:linear-gradient(120deg,#15211d 0%,#183c31 55%,#ef6c35 140%); color:white; margin-bottom:1rem; }
    .hero h1 { margin:0; font-size:2rem; letter-spacing:-.04em; }
    .hero p { margin:.35rem 0 0; color:#c9ddd5; }
    .stage { background:white; border:1px solid #e5e7e2; border-radius:16px; padding:1rem; min-height:115px; box-shadow:0 6px 20px rgba(20,35,29,.04); }
    .stage .eyebrow { color:#66736d; font-size:.74rem; text-transform:uppercase; letter-spacing:.08em; }
    .stage .value { color:#17231e; font-weight:750; font-size:1.3rem; margin-top:.35rem; }
    .stage .detail { color:#6c756f; font-size:.82rem; margin-top:.3rem; }
    .worker-id { display:inline-block; margin-top:.55rem; padding:.22rem .55rem; border:1px solid #668076; border-radius:99px; color:#dce9e3; font:12px monospace; }
    div[data-testid="stMetric"] { background:white; border:1px solid #e5e7e2; padding:.8rem 1rem; border-radius:14px; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_json(path: str, modified_at: float) -> dict[str, Any]:
    del modified_at
    return load_json(path)


def read_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return cached_json(str(path), path.stat().st_mtime)


def run_commands(steps: list[tuple[str, list[str]]]) -> bool:
    logs: list[dict[str, Any]] = []
    success = True
    with st.status("Đang chạy worker…", expanded=True) as status:
        for label, arguments in steps:
            st.write(f"**{label}**")
            result = subprocess.run(
                [sys.executable, "-m", *arguments],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
            )
            logs.append(
                {
                    "step": label,
                    "returncode": result.returncode,
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip(),
                }
            )
            if result.stdout.strip():
                st.code(result.stdout.strip(), language="json")
            if result.returncode:
                success = False
                st.error(result.stderr.strip() or "Worker thất bại")
                status.update(label="Worker dừng vì có lỗi", state="error")
                break
        if success:
            status.update(label="Worker hoàn tất", state="complete")
    st.session_state["pipeline_logs"] = logs
    st.cache_data.clear()
    return success


def discovered_urls(document: dict[str, Any]) -> list[str]:
    for page in document.get("pages", []):
        if not isinstance(page, dict):
            continue
        raw = page.get("raw_payload")
        links = raw.get("links") if isinstance(raw, dict) else None
        if isinstance(links, list):
            canonical = canonical_listing_urls(links)
            if canonical:
                return canonical
    return []


def supply_matches(products: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    ignored = {"and", "the", "for", "with", "a", "an", "custom", "personalized"}
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", query.lower())
        if len(token) > 2 and token not in ignored
    }
    if not tokens:
        return products
    ranked: list[tuple[int, dict[str, Any]]] = []
    for product in products:
        searchable = " ".join(
            str(product.get(field) or "").lower()
            for field in ("name", "category", "product_code", "options")
        )
        score = sum(token in searchable for token in tokens)
        if score:
            ranked.append((score, product))
    return [product for _, product in sorted(ranked, key=lambda row: -row[0])]


def normalize_step(worker: CrawlWorker) -> tuple[str, list[str]]:
    return (
        "Normalize dữ liệu worker",
        [
            "backend.cli.normalize",
            "--printway",
            str(PRINTWAY_PATH.relative_to(ROOT)),
            "--etsy",
            str(worker.etsy_path.relative_to(ROOT)),
            "--output",
            str(worker.normalized_path.relative_to(ROOT)),
        ],
    )


workers = REPOSITORY.list()
with st.sidebar:
    st.markdown("## ✦ Crawl workers")
    st.caption("Mỗi query có dữ liệu, tiến độ và dashboard riêng.")

    with st.form("create_worker", clear_on_submit=True):
        new_query = st.text_input(
            "Query worker mới",
            placeholder="Custom House Portrait Ornament",
        )
        create_submitted = st.form_submit_button("+ Tạo worker", width="stretch")
    if create_submitted:
        try:
            created = REPOSITORY.create(new_query)
            st.session_state["active_worker_selector"] = created.worker_id
            st.success(f"Đã tạo {created.worker_id}")
            st.rerun()
        except (OSError, ValueError) as exc:
            st.error(str(exc))

    workers = REPOSITORY.list()
    if not workers:
        st.info("Tạo worker đầu tiên để bắt đầu.")
        st.stop()

    worker_by_id = {worker.worker_id: worker for worker in workers}
    worker_ids = [worker.worker_id for worker in workers]
    if st.session_state.get("active_worker_selector") not in worker_ids:
        st.session_state["active_worker_selector"] = worker_ids[0]
    active_id = st.selectbox(
        "Dashboard đang xem",
        options=worker_ids,
        format_func=lambda value: worker_by_id[value].query,
        key="active_worker_selector",
    )
    worker = worker_by_id[active_id]
    manifest = REPOSITORY.manifest(active_id)
    st.caption(f"ID: `{worker.worker_id}` · status: `{worker.status}`")

    key_ready = bool(os.getenv("FIRECRAWL_API_KEY", "").strip())
    if key_ready:
        st.success("Firecrawl đã sẵn sàng")
    else:
        st.warning("Thiếu FIRECRAWL_API_KEY")
    active_location = firecrawl_location_payload()
    st.caption(
        f"Firecrawl region: 🇺🇸 {active_location['country']} · "
        f"{', '.join(active_location['languages'])}"
    )

    with st.expander("Cấu hình crawl"):
        batch_size = st.slider("Listing mỗi batch", 1, 25, 10)
        concurrency = st.slider("Concurrency", 1, 5, 2)
        proxy = st.selectbox("Proxy", ("auto", "basic", "enhanced"))
        per_page_budget = 1 if proxy == "basic" else 5
        planned_credits = batch_size * per_page_budget
        credit_budget = st.number_input(
            "Credit budget",
            min_value=planned_credits,
            value=planned_credits,
            step=1,
        )
        st.caption(f"Worst case detail batch: {planned_credits} credits")

    raw_before_action = read_optional(worker.etsy_path)
    discovered_before_action = len(discovered_urls(raw_before_action))
    details_before_action = sum(
        isinstance(page, dict) and page.get("page_type") == "listing_detail"
        for page in raw_before_action.get("pages", [])
    )
    remaining_before_action = max(0, discovered_before_action - details_before_action)
    if not worker.etsy_path.exists():
        next_label = "Crawl Etsy search"
        next_action = "search"
    elif remaining_before_action:
        next_label = f"Crawl {min(batch_size, remaining_before_action)} listing tiếp"
        next_action = "details"
    else:
        next_label = "Worker đã crawl xong"
        next_action = "complete"

    run_next = st.button(
        next_label,
        type="primary",
        width="stretch",
        disabled=not key_ready or next_action == "complete",
    )

    with st.expander("Quản trị catalog chung"):
        st.caption("Printway là nguồn supply dùng chung, không thuộc riêng một query.")
        refresh_printway = st.button("Cập nhật Printway", width="stretch")

if refresh_printway:
    if run_commands(
        [
            (
                "Cập nhật Printway catalog",
                ["backend.cli.printway", "--output", str(PRINTWAY_PATH.relative_to(ROOT))],
            )
        ]
    ):
        st.rerun()

if run_next:
    if next_action == "search":
        steps = [
            (
                "Crawl Etsy search",
                [
                    "backend.cli.etsy_firecrawl",
                    worker.query,
                    "--output",
                    str(worker.etsy_path.relative_to(ROOT)),
                    "--proxy",
                    proxy,
                    "--credit-budget",
                    str(per_page_budget),
                ],
            ),
            normalize_step(worker),
        ]
        target_status = "discovered"
    else:
        steps = [
            (
                "Crawl Etsy detail batch",
                [
                    "backend.cli.etsy_details",
                    str(worker.etsy_path.relative_to(ROOT)),
                    "--max-listings",
                    str(batch_size),
                    "--max-concurrency",
                    str(concurrency),
                    "--proxy",
                    proxy,
                    "--credit-budget",
                    str(int(credit_budget)),
                ],
            ),
            normalize_step(worker),
        ]
        target_status = "enriching"
    if run_commands(steps):
        latest_raw = load_json(worker.etsy_path)
        latest_discovered = len(discovered_urls(latest_raw))
        latest_details = sum(
            isinstance(page, dict) and page.get("page_type") == "listing_detail"
            for page in latest_raw.get("pages", [])
        )
        if latest_discovered and latest_details >= latest_discovered:
            target_status = "complete"
        REPOSITORY.update_status(worker.worker_id, target_status)
        st.rerun()

printway_raw = read_optional(PRINTWAY_PATH)
etsy_raw = read_optional(worker.etsy_path)
normalized = build_normalized_document(printway_raw, etsy_raw)
printway_products = normalized["printway_products"]
etsy_listings = normalized["etsy_listings"]
matching_supply = supply_matches(printway_products, worker.query)
listing_urls = discovered_urls(etsy_raw)
discovered = len(listing_urls)
detail_count = len(etsy_listings)
remaining = max(0, discovered - detail_count)
errors = etsy_raw.get("errors")
errors = errors if isinstance(errors, list) else []
detail_runs = etsy_raw.get("detail_runs")
detail_runs = detail_runs if isinstance(detail_runs, list) else []
credits_used = sum(
    int(run.get("credits_used") or 0) for run in detail_runs if isinstance(run, dict)
)
total_est_sales = sum(int(row.get("est_sales") or 0) for row in etsy_listings)
total_est_revenue = sum(float(row.get("est_revenue_usd") or 0) for row in etsy_listings)
monthly_est_sales = sum(int(row.get("avg_monthly_sales") or 0) for row in etsy_listings)
monthly_est_revenue = sum(
    float(row.get("avg_monthly_revenue_usd") or 0) for row in etsy_listings
)

st.markdown(
    f"""
    <div class="hero">
      <h1>{html.escape(worker.query)}</h1>
      <p>Dashboard chỉ hiển thị dữ liệu thuộc worker đang chọn.</p>
      <span class="worker-id">{html.escape(worker.worker_id)}</span>
    </div>
    """,
    unsafe_allow_html=True,
)

stage_columns = st.columns(4)
stage_values = [
    ("Supply phù hợp", f"{len(matching_supply):,}", "Từ Printway catalog chung"),
    ("Etsy discovery", f"{discovered:,}", "Unique listing links"),
    ("Etsy detail", f"{detail_count}/{discovered}", f"Còn {remaining} listing"),
    ("Worker runs", f"{len(detail_runs):,}", f"Đã dùng {credits_used} credits"),
]
for column, (eyebrow, value, detail) in zip(stage_columns, stage_values, strict=True):
    with column:
        st.markdown(
            f'<div class="stage"><div class="eyebrow">{eyebrow}</div>'
            f'<div class="value">{value}</div><div class="detail">{detail}</div></div>',
            unsafe_allow_html=True,
        )

if discovered:
    st.progress(detail_count / discovered, text=f"Tiến độ worker: {detail_count}/{discovered}")
elif not worker.etsy_path.exists():
    st.info("Worker mới chưa có dữ liệu. Bấm **Crawl Etsy search** ở thanh bên.")

overview_tab, estimates_tab, etsy_tab, supply_tab, operations_tab = st.tabs(
    [
        "Tổng quan worker",
        "Sales estimates",
        "Etsy listings",
        "Supply phù hợp",
        "Lịch sử & dữ liệu",
    ]
)

with overview_tab:
    metric_columns = st.columns(4)
    metric_columns[0].metric("Est. total sales", f"{total_est_sales:,}")
    metric_columns[1].metric("Est. monthly sales", f"{monthly_est_sales:,}")
    metric_columns[2].metric("Est. total revenue", f"${total_est_revenue:,.0f}")
    metric_columns[3].metric("Est. monthly revenue", f"${monthly_est_revenue:,.0f}")
    st.caption(
        "Ước tính từ review_count ÷ benchmark review rate. "
        "Monthly estimate đang dùng tuổi mặc định 365 ngày vì chưa có created_at đáng tin cậy."
    )
    st.markdown("### Etsy mới crawl của worker này")
    if not etsy_listings:
        st.caption("Chưa có listing detail.")
    card_columns = st.columns(4)
    for column, product in zip(card_columns, etsy_listings[:4], strict=False):
        with column:
            if product["image_url"]:
                st.image(product["image_url"], width="stretch")
            st.markdown(f"**{product['title']}**")
            rating = product["rating"] if product["rating"] is not None else "—"
            reviews = product["review_count"] if product["review_count"] is not None else 0
            st.caption(
                f"{product['shop']} · ⭐ {rating} · {reviews:,} lượt rating · "
                f"~{product['avg_monthly_sales']:,} sales/tháng · "
                f"~${product['avg_monthly_revenue_usd']:,.0f}/tháng"
            )
            st.markdown(f"[Mở trên Etsy ↗]({product['url']})")

with estimates_tab:
    st.markdown(f"### Sales estimates · {worker.query}")
    st.caption(
        "Heuristic estimate, không phải số bán được Etsy xác nhận. "
        "Tuổi listing hiện dùng benchmark 365 ngày."
    )
    with st.expander("Công thức đang dùng"):
        st.code(
            "est_sales = round(review_count / benchmark_review_rate)\n"
            "est_revenue = est_sales * price\n"
            "monthly_sales = round(est_sales / age_days * 30)\n"
            "monthly_revenue = monthly_sales * price",
            language="text",
        )
    ranked_estimates = sorted(
        etsy_listings,
        key=lambda row: float(row.get("avg_monthly_revenue_usd") or 0),
        reverse=True,
    )
    st.dataframe(
        ranked_estimates,
        width="stretch",
        hide_index=True,
        height=560,
        column_order=(
            "image_url",
            "title",
            "price",
            "review_count",
            "benchmark_category",
            "benchmark_review_rate",
            "est_sales",
            "est_revenue_usd",
            "avg_monthly_sales",
            "avg_monthly_revenue_usd",
            "estimate_age_days",
            "url",
        ),
        column_config={
            "image_url": st.column_config.ImageColumn("Ảnh", width="small"),
            "title": st.column_config.TextColumn("Sản phẩm", width="large"),
            "price": st.column_config.NumberColumn("Giá", format="$%.2f"),
            "review_count": st.column_config.NumberColumn("Reviews", format="%d"),
            "benchmark_review_rate": st.column_config.NumberColumn("Review rate", format="%.3f"),
            "est_sales": st.column_config.NumberColumn("Est. sales", format="%d"),
            "est_revenue_usd": st.column_config.NumberColumn("Est. revenue", format="$%.2f"),
            "avg_monthly_sales": st.column_config.NumberColumn("Sales/tháng", format="%d"),
            "avg_monthly_revenue_usd": st.column_config.NumberColumn("Revenue/tháng", format="$%.2f"),
            "estimate_age_days": st.column_config.NumberColumn("Age", format="%d ngày"),
            "url": st.column_config.LinkColumn("Link", display_text="Mở ↗"),
        },
    )

with etsy_tab:
    st.markdown(f"### {detail_count} listings · {worker.query}")
    etsy_filter = st.text_input("Lọc title/shop", key=f"etsy_filter_{worker.worker_id}").strip().lower()
    personalizable_only = st.checkbox("Chỉ sản phẩm personalization", key=f"personal_{worker.worker_id}")
    filtered_etsy = [
        product
        for product in etsy_listings
        if (
            not etsy_filter
            or etsy_filter in product["title"].lower()
            or etsy_filter in product["shop"].lower()
        )
        and (not personalizable_only or product["personalizable"])
    ]
    st.dataframe(
        filtered_etsy,
        width="stretch",
        hide_index=True,
        height=560,
        column_order=(
            "image_url",
            "title",
            "price",
            "shop",
            "rating",
            "review_count",
            "est_sales",
            "est_revenue_usd",
            "avg_monthly_sales",
            "avg_monthly_revenue_usd",
            "benchmark_category",
            "benchmark_review_rate",
            "estimate_age_days",
            "favorites",
            "ships_from",
            "personalizable",
            "url",
        ),
        column_config={
            "image_url": st.column_config.ImageColumn("Ảnh", width="small"),
            "title": st.column_config.TextColumn("Sản phẩm", width="large"),
            "price": st.column_config.NumberColumn("Giá", format="$%.2f"),
            "rating": st.column_config.NumberColumn("Rating", format="%.1f ⭐"),
            "review_count": st.column_config.NumberColumn("Lượt rating", format="%d"),
            "est_sales": st.column_config.NumberColumn("Est. sales", format="%d"),
            "est_revenue_usd": st.column_config.NumberColumn("Est. revenue", format="$%.2f"),
            "avg_monthly_sales": st.column_config.NumberColumn("Sales/tháng", format="%d"),
            "avg_monthly_revenue_usd": st.column_config.NumberColumn("Revenue/tháng", format="$%.2f"),
            "benchmark_review_rate": st.column_config.NumberColumn("Review rate (0–1)", format="%.3f"),
            "estimate_age_days": st.column_config.NumberColumn("Age benchmark", format="%d ngày"),
            "url": st.column_config.LinkColumn("Link", display_text="Mở ↗"),
        },
    )

with supply_tab:
    st.markdown(f"### Printway candidates cho “{worker.query}”")
    st.caption("Đây là catalog chung được xếp theo token của query; không trộn dữ liệu Etsy giữa workers.")
    supply_filter = st.text_input("Lọc thêm tên/category/code", key=f"supply_filter_{worker.worker_id}").strip().lower()
    filtered_supply = [
        product
        for product in matching_supply
        if not supply_filter
        or any(supply_filter in str(product.get(field) or "").lower() for field in ("name", "category", "product_code"))
    ]
    st.dataframe(
        filtered_supply,
        width="stretch",
        hide_index=True,
        height=560,
        column_order=("image_url", "name", "product_code", "starting_price_usd", "category", "production", "options", "url"),
        column_config={
            "image_url": st.column_config.ImageColumn("Ảnh", width="small"),
            "name": st.column_config.TextColumn("Sản phẩm", width="large"),
            "starting_price_usd": st.column_config.NumberColumn("Từ", format="$%.2f"),
            "url": st.column_config.LinkColumn("Link", display_text="Mở ↗"),
        },
    )

with operations_tab:
    st.markdown("### Mapping dữ liệu worker")
    st.json(
        {
            "worker_id": worker.worker_id,
            "query": worker.query,
            "status": manifest.get("status"),
            "etsy_raw": str(worker.etsy_path.relative_to(ROOT)),
            "normalized": str(worker.normalized_path.relative_to(ROOT)),
            "printway_shared": str(PRINTWAY_PATH.relative_to(ROOT)),
        },
        expanded=False,
    )
    st.markdown("### Detail batch runs")
    run_rows = [
        {
            "status": run.get("status"),
            "batch_job_id": run.get("batch_job_id"),
            "stored": run.get("listing_pages_stored"),
            "errors": run.get("listing_errors", 0),
            "credits": run.get("credits_used"),
            "requests": run.get("requests_count"),
            "finished_at": run.get("finished_at"),
        }
        for run in detail_runs
        if isinstance(run, dict)
    ]
    st.dataframe(run_rows, width="stretch", hide_index=True)
    if errors:
        st.error(f"Có {len(errors)} crawler errors")
        st.dataframe(errors, width="stretch", hide_index=True)
    else:
        st.success("Không có crawler error")
    st.download_button(
        "Tải normalized JSON của worker",
        json.dumps(normalized, ensure_ascii=False, indent=2),
        file_name=f"{worker.worker_id}_normalized.json",
        mime="application/json",
        width="stretch",
    )
    logs = st.session_state.get("pipeline_logs", [])
    if logs:
        with st.expander("Log lần chạy gần nhất"):
            st.json(logs)
