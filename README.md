# PW1 — R&D Market Intelligence Agent

The current implementation is intentionally limited to the Etsy crawler foundation. Google
Trends and the intelligence pipeline are not implemented yet.

## Etsy vertical slice

```text
keyword
  -> EtsyAdapter
  -> shared HTTP transport (timeout, retry, rate limit)
  -> Etsy active-listing API
  -> validated RawProduct records
  -> immutable SQLite observations
```

The Etsy adapter uses the official `findAllListingsActive` endpoint. It does not scrape Etsy
web pages. Public Etsy API requests require an app API key in the
`keystring:shared_secret` format.

## Requirements

- Python 3.11+
- `httpx`
- `pytest` and `pytest-asyncio` for tests

Install the project and test dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

## Fast offline tests

The default suite uses saved Etsy fixtures and does not call the internet:

```bash
pytest -m 'not live'
```

## Configure Etsy credentials

Copy `.env.example` values into your shell or secret manager. The application supports either
one combined value:

```bash
export ETSY_API_KEY='your_keystring:your_shared_secret'
```

or two separate values:

```bash
export ETSY_KEYSTRING='your_keystring'
export ETSY_SHARED_SECRET='your_shared_secret'
```

Credentials are never written to the crawler database.

## Run a live smoke test

Live tests are opt-in and fetch only one page:

```bash
export RUN_ETSY_LIVE=1
pytest -m live tests/smoke/test_etsy_live.py
```

## Run the crawler

```bash
python -m backend.cli.etsy \
  'Christmas Ornament' \
  --database pw1_etsy.sqlite3 \
  --page-size 25 \
  --max-pages 1
```

The command prints a run summary. Raw observations and source-run status are stored in the
selected SQLite database. SQLite is the fast local implementation of the repository contract;
the crawler itself is not coupled to SQLite.

See [docs/etsy-crawler.md](docs/etsy-crawler.md) for the design and manual verification
checklist.

## Export the Printway catalog to JSON

The exporter reads the same public catalog endpoint used by Printway's storefront. It keeps the
raw product objects, including IDs, slugs, regional prices, locations, categories, options, image
URLs, variants, and product codes.

```bash
python -m backend.cli.printway \
  --output data/printway_catalog.json \
  --page-size 100
```

No Printway login or cookie is stored. The exporter validates that the reported count equals the
number of unique products before atomically replacing the JSON file.

## Worker-scoped Streamlit dashboard

Each Etsy query is a first-class worker with its own manifest, raw crawl file, normalized artifact,
progress, run history, and dashboard. Printway remains a shared supply catalog and is referenced
explicitly from every worker manifest.

```text
data/workers/<worker_id>/
  worker.json       # query, status, and data mapping
  etsy.json         # search plus listing-detail pages for this worker only
  normalized.json   # decision fields for this worker only
```

Create workers from the dashboard or CLI:

```bash
python3 -m backend.cli.workers create 'Custom House Portrait Ornament'
python3 -m backend.cli.workers list
```

Import a legacy keyword JSON without modifying the original:

```bash
python3 -m backend.cli.workers import \
  'Christmas Ornament' \
  data/etsy_christmas_ornament.json
```

```bash
export FIRECRAWL_API_KEY='fc-your-api-key'
python3 -m streamlit run streamlit_app.py --server.port 8502
```

Open `http://localhost:8502`, select one active worker, then use its single next-step action. A new
worker first crawls Etsy search; later clicks crawl the next resumable detail batch. Normalization
runs automatically after either action. Every metric, Etsy table, run, error, and download is
scoped to the selected worker. Provider markdown, tokens, nonces, and other raw implementation
fields are not displayed.

All Firecrawl request types explicitly set `location` to `{"country":"US","languages":["en-US"]}`.
This applies to Etsy search scrape, listing batch scrape, and generic multi-page crawl requests;
the selected proxy mode (`basic`, `enhanced`, or `auto`) does not change that market region.

Generate the same normalized artifact without the UI:

```bash
python3 -m backend.cli.normalize \
  --printway data/printway_catalog.json \
  --etsy data/workers/christmas_ornament/etsy.json \
  --output data/workers/christmas_ornament/normalized.json
```

### Etsy sales estimates

Normalized Etsy rows include heuristic sales and revenue estimates derived from listing review
count, price, a keyword-matched category review-rate benchmark, and listing age:

```text
est_sales = round(review_count / benchmark_review_rate)
est_revenue_usd = est_sales * price
avg_monthly_sales = round(est_sales / age_days * 30)
avg_monthly_revenue_usd = avg_monthly_sales * price
```

The current crawl does not expose a reliable listing creation timestamp, so `age_days` defaults
to 365 and `estimate_age_source` is stored as `benchmark_default`. These fields are estimates for
ranking and comparison, not verified Etsy sales. When trustworthy listing age or views become
available, the engine also accepts them and can calculate conversion rate.

## Firecrawl worker pipeline

The Firecrawl provider is a separate, bounded worker pipeline. It starts a Firecrawl API v2 crawl,
polls the durable provider job, stores immutable pages, enforces a worst-case credit budget, and
reuses recent successful runs through a local cache.

```text
bounded request
  -> POST /v2/crawl
  -> provider job ID
  -> poll GET /v2/crawl/{id}
  -> immutable raw pages
  -> completed/failed local run
```

Configure the key in your shell; never commit it:

```bash
export FIRECRAWL_API_KEY='fc-your-api-key'
```

Run a small crawl and store its complete page payloads in JSON (the default storage mode):

```bash
python -m backend.cli.firecrawl \
  'https://example.com/' \
  --output-json data/firecrawl.json \
  --max-depth 0 \
  --limit 1 \
  --proxy basic \
  --credit-budget 1
```

SQLite remains available only when explicitly requested with `--database`.

For the current Etsy proof of concept, pass a keyword instead of assembling the search URL. This
uses Firecrawl's single-page `/v2/scrape` endpoint, not the multi-page crawl job endpoint:

```bash
python -m backend.cli.etsy_firecrawl \
  'Christmas Ornament' \
  --output data/etsy_christmas_ornament.json \
  --proxy basic \
  --force-refresh
```

This bounded first slice scrapes one Etsy search-results page and saves Firecrawl's markdown,
listing links, and raw response in JSON. Expanding listing links into detail-page jobs is
handled by a separate resumable command:

```bash
python -m backend.cli.etsy_details \
  data/etsy_christmas_ornament.json \
  --max-listings 10 \
  --max-concurrency 2 \
  --proxy auto \
  --credit-budget 50
```

The detail worker canonicalizes tracking/variation URLs by Etsy listing ID, submits only listings
not already stored, records the Firecrawl batch job and errors, and atomically updates the same
JSON file. Run the command again to process the next ten listings. Use `--proxy basic
--credit-budget 10` for the cheaper one-credit-per-listing attempt.
Etsy may still return a CAPTCHA or block a request; Firecrawl is a delivery layer, not a guarantee
against target-site restrictions.

Run the opt-in one-credit live smoke test:

```bash
export RUN_FIRECRAWL_LIVE=1
pytest -m live tests/smoke/test_firecrawl_live.py
```

See [docs/firecrawl-worker.md](docs/firecrawl-worker.md) for architecture, safety limits, and the
verification checklist. Firecrawl must not be used to bypass a target site's API, robots policy,
terms, authentication, or access controls.
