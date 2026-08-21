from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.models import RawProduct


class SQLiteRawProductRepository:
    """Local repository for fast tests and demos; the repository port stays DB-agnostic."""

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
                CREATE TABLE IF NOT EXISTS source_runs (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    records_collected INTEGER NOT NULL DEFAULT 0,
                    requests_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_type TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS raw_product_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_run_id TEXT NOT NULL REFERENCES source_runs(id),
                    source TEXT NOT NULL,
                    source_product_id TEXT NOT NULL,
                    source_url TEXT,
                    title TEXT NOT NULL,
                    price TEXT,
                    currency TEXT,
                    shop_id TEXT,
                    rating TEXT,
                    reviews INTEGER,
                    raw_payload TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(source_run_id, source, source_product_id)
                );

                CREATE TRIGGER IF NOT EXISTS raw_products_no_update
                BEFORE UPDATE ON raw_product_observations
                BEGIN
                    SELECT RAISE(ABORT, 'raw product observations are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS raw_products_no_delete
                BEFORE DELETE ON raw_product_observations
                BEGIN
                    SELECT RAISE(ABORT, 'raw product observations are immutable');
                END;
                """
            )

    async def start_run(self, run_id: str, *, source: str, query: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_runs(id, source, query, status, started_at)
                VALUES (?, ?, ?, 'running', ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (run_id, source, query, now),
            )

    async def save_products(self, run_id: str, products: tuple[RawProduct, ...]) -> int:
        inserted = 0
        with self._connect() as connection:
            for product in products:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_product_observations(
                        source_run_id, source, source_product_id, source_url, title,
                        price, currency, shop_id, rating, reviews, raw_payload, observed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        product.source,
                        product.source_product_id,
                        product.source_url,
                        product.title,
                        str(product.price) if product.price is not None else None,
                        product.currency,
                        product.shop_id,
                        str(product.rating) if product.rating is not None else None,
                        product.reviews,
                        json.dumps(product.raw_payload, ensure_ascii=False, sort_keys=True),
                        product.observed_at.isoformat(),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    async def mark_succeeded(
        self,
        run_id: str,
        *,
        records_collected: int,
        requests_count: int,
        retry_count: int,
    ) -> None:
        self._update_run(
            run_id,
            status="succeeded",
            records_collected=records_collected,
            requests_count=requests_count,
            retry_count=retry_count,
        )

    async def mark_failed(
        self,
        run_id: str,
        *,
        records_collected: int,
        error_type: str,
        error_message: str,
        requests_count: int,
        retry_count: int,
    ) -> None:
        self._update_run(
            run_id,
            status="failed",
            records_collected=records_collected,
            requests_count=requests_count,
            retry_count=retry_count,
            error_type=error_type,
            error_message=error_message,
        )

    def _update_run(
        self,
        run_id: str,
        *,
        status: str,
        records_collected: int | None,
        requests_count: int,
        retry_count: int,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE source_runs
                SET status = ?,
                    records_collected = COALESCE(?, records_collected),
                    requests_count = ?,
                    retry_count = ?,
                    error_type = ?,
                    error_message = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    records_collected,
                    requests_count,
                    retry_count,
                    error_type,
                    error_message,
                    datetime.now(UTC).isoformat(),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_products(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows: Iterable[sqlite3.Row] = connection.execute(
                """
                SELECT * FROM raw_product_observations
                WHERE source_run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
