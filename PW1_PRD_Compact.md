# PW1 — R&D Market Intelligence Agent

## 1. Product Overview

**Tên tạm:** Printway R&D Intelligence Agent  
**Mục tiêu:** Nhận keyword/topic → crawl dữ liệu từ **ít nhất 2 marketplace** → lưu raw data → chuẩn hóa → AI Agent phân tích → trả về **Top Keywords, Top Products, Key Insights, Opportunity Score, 30-Day Forecast, R&D Recommendation**.

```text
Research Query
   ↓
Crawler Orchestrator
   ↓
Source Worker #1 + Source Worker #2
   ↓
Raw Database
   ↓
Cleaning / Normalization
   ↓
Analytics Dataset
   ↓
AI Research Agent
   ↓
R&D Intelligence Report
```

## 2. Problem Statement

R&D hiện phải nghiên cứu thủ công trên nhiều marketplace/tool để tìm sản phẩm bán tốt, keyword tăng trưởng, niche tiềm năng, trend mới và quyết định sản phẩm nên phát triển. Quy trình phân mảnh, chậm và phụ thuộc nhiều vào người research.

**PW1 = intelligence layer** nằm trên các nguồn dữ liệu đó.

## 3. Target User

- **Primary:** Printway R&D team, non-tech.
- **Future:** Seller personalization.
- **MVP không làm:** seller-specific recommendation.

## 4. Product Goals

| Goal | Acceptance |
|---|---|
| G1 | Crawl thành công ≥2 nguồn |
| G2 | Raw records được lưu DB |
| G3 | Raw → cleaned/normalized dataset |
| G4 | AI Agent đọc dữ liệu và phân tích |
| G5 | Có forecast + actionable recommendation |

### Non-goals
- Không làm 7 marketplace.
- Không làm ML forecast phức tạp.
- Không làm state-level US analysis.
- Không làm full competitor tracker.
- Không làm production-grade anti-bot platform.
- Không tối ưu UI quá sâu trước khi pipeline chạy.

## 5. User Journey

### Step 1 — Create Research

```text
Research Topic: Christmas Ornament
Market: United States
Period: Last 90 days
Sources: Source A, Source B
[Start Research]
```

### Step 2 — Crawl Progress

```text
Source A: 382 records ✓
Source B: 247 records ✓
Total: 629 raw records
Crawl time: 18.2s
```

### Step 3 — Processing

```text
✓ Raw data stored
✓ Cleaning
✓ Deduplication
✓ Normalization
● AI analysis
○ Forecast
○ Recommendation
```

### Step 4 — Result

- Top Keywords
- Top Products
- Key Insights
- Opportunity Score
- 30-Day Forecast
- R&D Recommendation

### Step 5 — Evidence

`Why this recommendation?` → xem evidence/source data.

### Step 6 — Export

- P0: Markdown
- P1: PDF

## 6. Functional Requirements

### FR-01 — Research Job
Input tối thiểu:
- keyword/topic
- sources

Optional:
- market
- time range

### FR-02 — Crawler Orchestrator (P0)

```text
crawl(query, options) → RawProduct[]
```

RawProduct tối thiểu:

```json
{
  "source": "source_a",
  "source_product_id": "...",
  "url": "...",
  "title": "...",
  "price": 19.99,
  "rating": 4.8,
  "reviews": 1250,
  "raw_payload": {},
  "crawled_at": "..."
}
```

### FR-03 — Raw Data Storage (P0)

**crawl_jobs**
- id
- query
- status
- started_at
- finished_at
- total_records
- crawl_duration

**raw_products**
- id
- crawl_job_id
- source
- source_product_id
- source_url
- raw_payload JSONB
- crawled_at

> Raw data không bị sửa sau khi crawl.

### FR-04 — Cleaning & Normalization (P0)

```text
RAW → Validation → Cleaning → Deduplication → Normalization → Enrichment
```

Normalized schema:

```text
source
product_id
title
keyword
price
currency
rating
reviews
sales_estimate
revenue_estimate
demand
competition
growth
collection
category
material
observed_at
```

Field thiếu → `null`, không invent data.

### FR-05 — Keyword Intelligence (P0)

| Keyword | Demand | Growth | Collection | Product Suggestion |
|---|---:|---:|---|---|
| baby first christmas ornament | 90 | 70 | Christmas | Ornament |
| pet christmas ornament | 87 | 78 | Christmas | Acrylic Ornament |

### FR-06 — Product Intelligence (P0)

| Product | Revenue | Quantity | Growth | Opportunity |
|---|---:|---:|---:|---:|
| Baby First Ornament | $18.9K | 842 | +34% | 90 |
| Pet Memorial Ornament | $16.4K | 721 | +28% | 87 |

Optional filter nếu dữ liệu support:
- 7D
- 30D
- 90D
- Last Year

### FR-07 — Opportunity Score

MVP formula:

```text
Opportunity =
Demand 30%
+ Growth 25%
+ Competition 20%
+ Revenue 15%
+ Seasonality 10%
```

Nếu có Printway catalog → thêm Manufacturing Fit.

**Rule:** Python/SQL tính score, LLM chỉ explain.

### FR-08 — AI Research Agent (P0)

Agent tools:

```text
get_research_job()
get_top_keywords()
get_top_products()
get_market_statistics()
get_growth_signals()
get_opportunity_scores()
get_source_evidence()
get_printway_catalog()
```

Flow:

```text
Research Job → Read Data → Analyze → Detect Signals → Explain → Forecast → Recommend
```

### FR-09 — Key Insights (P0)

Mỗi insight có:
- claim
- reason
- evidence[]
- confidence

### FR-10 — 30-Day Forecast (P0)

```text
Historical signals + Recent momentum + Seasonality → Forecast
```

Output:

```text
Current Demand: 90
30d Forecast: 94
Direction: Rising
Confidence: High
```

### FR-11 — R&D Recommendation (P0)

```text
Develop: Baby First Christmas Acrylic Ornament
Target niche: New Parents
Why: strong demand + growth + seasonality
Action: develop 5–10 personalized variants
Timing: start development now
Confidence: High
```

### FR-12 — Report

```text
1. Executive Summary
2. Data Sources
3. Top Keywords
4. Top Products
5. Key Insights
6. Opportunity Analysis
7. 30-Day Forecast
8. R&D Recommendation
9. Evidence / Methodology
```

### FR-13 — Evidence Viewer
Cho phép drill-down từ insight/recommendation về raw/normalized records.

### FR-14 — Crawler Metrics

```text
records_collected
requests_count
crawl_duration
failed_requests
retry_count
cache_hits
browser_sessions
estimated_cost
```

## 7. Architecture

```text
Frontend
   ↓
FastAPI
   ↓
Research Service
   ├── Crawler Queue
   │    ├── Worker A
   │    └── Worker B
   │
   └── Analysis Queue
        └── AI Agent

PostgreSQL
   ↓
Report Generator
```

### Stack
- Python + FastAPI
- PostgreSQL
- Redis + Celery/ARQ
- HTTP/API first, Playwright fallback
- Polars/Pandas
- LLM tool calling + structured output
- Next.js hoặc Streamlit
- Markdown → PDF

## 8. Repo Structure

```text
pw1/
├── backend/
│   ├── api/
│   ├── crawler/
│   │   ├── base.py
│   │   ├── source_a.py
│   │   └── source_b.py
│   ├── workers/
│   ├── processing/
│   │   ├── cleaner.py
│   │   ├── normalizer.py
│   │   └── deduplicator.py
│   ├── intelligence/
│   │   ├── keyword.py
│   │   ├── product.py
│   │   ├── opportunity.py
│   │   └── forecast.py
│   ├── agent/
│   │   ├── tools.py
│   │   └── research_agent.py
│   └── reports/
├── frontend/
├── data/fixtures/
├── tests/
├── docker-compose.yml
├── .env.example
└── README.md
```

## 9. Backlog

### P0 — Must Have

| ID | Task | Owner | Est. |
|---|---|---|---:|
| P0-01 | Research Job API | Dev 3 | 1h |
| P0-02 | DB schema | Dev 3 | 1h |
| P0-03 | Crawler base interface | Dev 1 | 0.5h |
| P0-04 | Source Worker #1 | Dev 1 | 3h |
| P0-05 | Source Worker #2 | Dev 1 | 3h |
| P0-06 | Raw data persistence | Dev 1/3 | 1h |
| P0-07 | Cleaning pipeline | Dev 2 | 1.5h |
| P0-08 | Normalization | Dev 2 | 2h |
| P0-09 | Keyword metrics | Dev 2 | 2h |
| P0-10 | Product metrics | Dev 2 | 2h |
| P0-11 | Opportunity Score | Dev 2 | 1.5h |
| P0-12 | Agent tools | Dev 2 | 2h |
| P0-13 | AI Research Agent | Dev 2 | 2h |
| P0-14 | Key Insights | Dev 2 | 1h |
| P0-15 | 30-day Forecast | Dev 2 | 1.5h |
| P0-16 | Recommendation | Dev 2 | 1h |
| P0-17 | Job progress UI | Dev 3 | 2h |
| P0-18 | Results dashboard | Dev 3 | 3h |
| P0-19 | Markdown report | Dev 3 | 1h |
| P0-20 | Integration test | ALL | 2h |

### P1 — Sau khi end-to-end chạy
- Evidence viewer
- Crawl performance metrics
- Cache
- Incremental crawl
- Parallel workers
- Retry/backoff
- Historical filter
- Printway Manufacturing Fit
- PDF export

### P2 — Nếu còn thời gian
- Google Trends
- Design Insight
- Competitor Tracker
- Natural-language chat
- Seller personalization
- More marketplaces
- Advanced forecasting

## 10. Plan 2 Ngày / 3 Dev

### Day 1 — Data chạy end-to-end

**0–1h**
- Lock 2 sources
- Lock schema
- Lock API contract
- Create repo
- Create DB

**Hour 1–5**
- Dev 1: 2 crawler workers
- Dev 2: processing + metrics
- Dev 3: DB + Research API + skeleton UI

**Checkpoint #1**

```text
POST /research → worker → RAW DB
```

Phải chạy.

**Hour 5–8**
- Dev 1: retry + cache + crawler reliability
- Dev 2: keyword/product/opportunity
- Dev 3: job progress + result UI

**Day 1 DoD**
- User nhập keyword
- ≥2 workers chạy
- Raw data thật được lưu
- Processing chạy
- UI có basic result

### Day 2 — Intelligence + Demo

**Hour 0–3**
- Dev 2: Agent + Insights + Forecast + Recommendation
- Dev 1: Crawler metrics + cache + speed
- Dev 3: Dashboard + Report + Evidence

**Checkpoint #2**

```text
Keyword → Crawler → DB → Processing → Agent → Report
```

Full flow phải chạy.

**Hour 3–5**
- Fix errors
- Data quality
- Explainability
- Performance
- UI polish

**Sau Hour 5: FREEZE FEATURE**
- Demo
- README
- Slides
- Video
- Test
- Fallback

## 11. Demo Acceptance Test

1. Nhập `Christmas Ornament`.
2. Bấm `Start Research`.
3. Thấy 2 crawler workers chạy.
4. Thấy raw records tăng.
5. Mở raw data.
6. Processing hoàn tất.
7. Xem Top Keywords.
8. Xem Top Products.
9. Xem Opportunity Score.
10. Đọc Key Insights.
11. Xem 30-Day Forecast.
12. Nhận R&D Recommendation.
13. Click evidence.
14. Export report.

**Pass khi flow chạy ổn 3 lần liên tiếp.**

## 12. Core Principle

```text
1. WE CRAWLED IT
        ↓
2. AI ANALYZED IT
        ↓
3. R&D CAN ACT ON IT
```

Nếu một feature không giúp chứng minh một trong 3 câu trên → không làm trong MVP 2 ngày.

## 13. Effort Allocation

- 40% Crawler + Data Pipeline
- 35% AI/Data Analysis
- 15% Output/Report
- 10% UI

