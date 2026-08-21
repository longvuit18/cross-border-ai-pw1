# Firecrawl worker pipeline

## Scope

This worker integrates with Firecrawl API v2 for bounded crawls of URLs the operator is permitted
to access. It is not a replacement for the official Etsy API and must not be used to bypass Etsy
API quotas, robots directives, authentication, CAPTCHA, or access controls.

## Pipeline

1. Validate URL, crawl depth, page limit, proxy strategy, and credit budget.
2. Reuse a recent local successful JSON or SQLite run when the cache key matches.
3. Submit `POST /v2/crawl` and persist the provider job ID.
4. Poll `GET /v2/crawl/{id}` until completed, failed, cancelled, or timed out.
5. Store every returned page using `(run_id, source_url)` idempotency.
6. Follow Firecrawl's `next` result URL only when it remains on the configured API origin.
7. Cancel the provider job on local timeout or observed credit-budget overrun.
8. Preserve partial pages and terminal error information for audit.

## Safety defaults

| Setting | Default | Purpose |
|---|---:|---|
| `limit` | 10 pages | Prevent accidental site-wide crawls |
| `maxDiscoveryDepth` | 1 | Bound link discovery |
| `proxy` | `auto` | Let Firecrawl choose basic/enhanced |
| `credit_budget` | 50 | Covers worst-case 5 credits × 10 pages |
| `maxAge` | 24 hours | Prefer provider cache |
| local cache TTL | 24 hours | Avoid duplicate provider jobs |
| request rate | 2/second | Bound API polling pressure |

For `auto` or `enhanced`, validation assumes up to five credits per page. A request whose planned
worst-case cost exceeds `credit_budget` is rejected before contacting Firecrawl.

## Persistence

JSON is the CLI default for this proof of concept:

```bash
python -m backend.cli.firecrawl 'https://example.com/' \
  --output-json data/firecrawl.json \
  --max-depth 0 --limit 1 --proxy basic --credit-budget 1
```

The JSON document contains `run` metadata plus a `pages` array. Each page keeps its source URL,
title, markdown, full Firecrawl payload, and observation timestamp. Writes use a temporary file
and atomic rename, so a process never exposes a half-written document.

SQLite is retained as an explicitly selected alternative:

### `firecrawl_runs`

Stores the local run, cache key, provider job ID, status, progress, credits, request/retry counts,
timestamps, and terminal error.

### `raw_crawl_pages`

Stores source URL, title, markdown, original page JSON, and observation time. Database triggers
reject updates and deletes. A unique constraint prevents duplicate pages within a run.

## Test layers

Offline tests do not consume Firecrawl credits:

```bash
pytest -m 'not live'
```

The live smoke test is opt-in and is deliberately limited to one `example.com` page using the
basic proxy and a one-credit budget:

```bash
export FIRECRAWL_API_KEY='fc-your-api-key'
export RUN_FIRECRAWL_LIVE=1
pytest -m live tests/smoke/test_firecrawl_live.py
```

## Verification checklist

- [ ] Rotate any API key that was shared in chat or another non-secret channel.
- [ ] Export the replacement key as `FIRECRAWL_API_KEY`.
- [ ] Run all offline tests.
- [ ] Run the one-credit live smoke test.
- [ ] Confirm `pages_stored=1` and `credits_used<=1`.
- [ ] Confirm no API key appears in stdout, JSON, SQLite, or test reports.
- [ ] Confirm repeated CLI calls return `cache_hit=true` during the configured TTL.
- [ ] Confirm every production target permits automated collection.
