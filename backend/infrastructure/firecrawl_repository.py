from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.domain.firecrawl_models import FirecrawlPage, FirecrawlSummary


class SQLiteFirecrawlRepository:
    """Durable local worker state and immutable Firecrawl page storage."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    async def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS firecrawl_runs (
                    id TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL,
                    start_url TEXT NOT NULL,
                    provider_job_id TEXT,
                    status TEXT NOT NULL,
                    provider_status TEXT,
                    total INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    credits_used INTEGER NOT NULL DEFAULT 0,
                    pages_stored INTEGER NOT NULL DEFAULT 0,
                    requests_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE INDEX IF NOT EXISTS firecrawl_runs_cache_idx
                ON firecrawl_runs(cache_key, status, started_at);

                CREATE TABLE IF NOT EXISTS raw_crawl_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES firecrawl_runs(id),
                    source_url TEXT NOT NULL,
                    title TEXT,
                    markdown TEXT,
                    raw_payload TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(run_id, source_url)
                );

                CREATE TRIGGER IF NOT EXISTS raw_crawl_pages_no_update
                BEFORE UPDATE ON raw_crawl_pages
                BEGIN
                    SELECT RAISE(ABORT, 'raw crawl pages are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS raw_crawl_pages_no_delete
                BEFORE DELETE ON raw_crawl_pages
                BEGIN
                    SELECT RAISE(ABORT, 'raw crawl pages are immutable');
                END;
                """
            )

    async def find_cached_summary(
        self,
        cache_key: str,
        *,
        ttl_seconds: int,
    ) -> FirecrawlSummary | None:
        if ttl_seconds <= 0:
            return None
        threshold = (datetime.now(UTC) - timedelta(seconds=ttl_seconds)).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM firecrawl_runs
                WHERE cache_key = ? AND status = 'succeeded' AND started_at >= ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (cache_key, threshold),
            ).fetchone()
        return self._summary_from_row(row, cache_hit=True) if row else None

    async def start_run(
        self,
        run_id: str,
        *,
        cache_key: str,
        start_url: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO firecrawl_runs(id, cache_key, start_url, status, started_at)
                VALUES (?, ?, ?, 'starting', ?)
                """,
                (run_id, cache_key, start_url, datetime.now(UTC).isoformat()),
            )

    async def attach_provider_job(
        self,
        run_id: str,
        provider_job_id: str,
        *,
        requests_count: int,
        retry_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE firecrawl_runs
                SET provider_job_id = ?, status = 'running', provider_status = 'scraping',
                    requests_count = ?, retry_count = ?
                WHERE id = ?
                """,
                (provider_job_id, requests_count, retry_count, run_id),
            )

    async def save_pages(self, run_id: str, pages: tuple[FirecrawlPage, ...]) -> int:
        inserted = 0
        with self._connect() as connection:
            for page in pages:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_crawl_pages(
                        run_id, source_url, title, markdown, raw_payload, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        page.source_url,
                        page.title,
                        page.markdown,
                        json.dumps(page.raw_payload, ensure_ascii=False, sort_keys=True),
                        page.observed_at.isoformat(),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    async def update_progress(
        self,
        run_id: str,
        *,
        provider_status: str,
        total: int,
        completed: int,
        credits_used: int,
        pages_stored: int,
        requests_count: int,
        retry_count: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE firecrawl_runs
                SET provider_status = ?, total = ?, completed = ?, credits_used = ?,
                    pages_stored = ?, requests_count = ?, retry_count = ?
                WHERE id = ?
                """,
                (
                    provider_status,
                    total,
                    completed,
                    credits_used,
                    pages_stored,
                    requests_count,
                    retry_count,
                    run_id,
                ),
            )

    async def mark_succeeded(self, run_id: str) -> FirecrawlSummary:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE firecrawl_runs
                SET status = 'succeeded', provider_status = 'completed', finished_at = ?
                WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), run_id),
            )
            row = connection.execute(
                "SELECT * FROM firecrawl_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Firecrawl run disappeared before completion")
        return self._summary_from_row(row, cache_hit=False)

    async def mark_failed(
        self,
        run_id: str,
        *,
        error_type: str,
        error_message: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE firecrawl_runs
                SET status = 'failed', error_type = ?, error_message = ?, finished_at = ?
                WHERE id = ?
                """,
                (error_type, error_message, datetime.now(UTC).isoformat(), run_id),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM firecrawl_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_pages(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM raw_crawl_pages WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _summary_from_row(row: sqlite3.Row, *, cache_hit: bool) -> FirecrawlSummary:
        finished_at = row["finished_at"] or datetime.now(UTC).isoformat()
        return FirecrawlSummary(
            run_id=row["id"],
            provider_job_id=row["provider_job_id"] or "",
            status=row["status"],
            pages_stored=row["pages_stored"],
            total=row["total"],
            completed=row["completed"],
            credits_used=row["credits_used"],
            requests_count=row["requests_count"],
            retry_count=row["retry_count"],
            cache_hit=cache_hit,
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(finished_at),
        )

