# Etsy crawler foundation

## Scope

This slice collects active Etsy listings by keyword, stores immutable raw observations, and
records an auditable source run. It deliberately excludes Google Trends, analytics, forecasting,
the AI agent, and the frontend.

## Components

| Component | Responsibility |
|---|---|
| `EtsyAdapter` | Etsy request parameters, pagination, listing parsing, source capabilities |
| `HttpxJsonTransport` | timeout, retry, `Retry-After`, status mapping, JSON validation |
| `AsyncRateLimiter` | configurable per-process request pacing |
| `EtsyCrawler` | page orchestration, loop protection, run lifecycle, persistence |
| `SQLiteRawProductRepository` | fast local source-run and append-only raw storage |

The repository and page source are protocols. A PostgreSQL repository can replace SQLite without
changing `EtsyCrawler` or `EtsyAdapter`.

## Persisted tables

### `source_runs`

Tracks query, status, request count, retry count, record count, timestamps, and terminal errors.

### `raw_product_observations`

Stores the Etsy listing ID, URL, title, price, shop ID, original JSON payload, and observation
timestamp. Database triggers reject updates and deletes to preserve raw evidence. A unique key on
`(source_run_id, source, source_product_id)` prevents duplicates within one run.

## Reliability behavior

- Retries network failures, HTTP 429, and selected HTTP 5xx responses.
- Honors Etsy's `Retry-After` header.
- Does not retry HTTP 400, 401, or 403 responses.
- Caps pages per run and detects pagination that does not advance.
- Keeps missing unsupported fields such as listing rating/reviews as `null`.
- Never writes credentials to raw payloads or run records.

## Manual verification checklist

- [ ] Register or select an Etsy developer app.
- [ ] Export `ETSY_API_KEY=keystring:shared_secret`.
- [ ] Run `pytest -m 'not live'`.
- [ ] Export `RUN_ETSY_LIVE=1`.
- [ ] Run `pytest -m live tests/smoke/test_etsy_live.py`.
- [ ] Run the CLI for `Christmas Ornament` with one page.
- [ ] Confirm the summary status is `succeeded`.
- [ ] Confirm the record count is greater than zero.
- [ ] Confirm raw rows contain `source_product_id`, `source_url`, and `raw_payload`.
- [ ] Confirm no Etsy credentials appear in stdout or the SQLite database.
- [ ] Approve the Etsy slice before work begins on Google Trends.
