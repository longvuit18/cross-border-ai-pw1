from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.domain.firecrawl_models import FirecrawlPage, FirecrawlSummary


class JsonFirecrawlRepository:
    """One-file Firecrawl run storage for inspectable local experiments."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self._document: dict[str, Any] = {}

    async def initialize(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    async def find_cached_summary(
        self,
        cache_key: str,
        *,
        ttl_seconds: int,
    ) -> FirecrawlSummary | None:
        if ttl_seconds <= 0 or not self.output_path.exists():
            return None
        try:
            document = json.loads(self.output_path.read_text(encoding="utf-8"))
            run = document["run"]
            started_at = datetime.fromisoformat(run["started_at"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        threshold = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
        if (
            run.get("cache_key") != cache_key
            or run.get("status") != "succeeded"
            or started_at < threshold
        ):
            return None
        self._document = document
        return self._summary(cache_hit=True)

    async def start_run(
        self,
        run_id: str,
        *,
        cache_key: str,
        start_url: str,
    ) -> None:
        self._document = {
            "schema_version": 1,
            "source": "firecrawl",
            "run": {
                "id": run_id,
                "cache_key": cache_key,
                "start_url": start_url,
                "provider_job_id": None,
                "status": "starting",
                "provider_status": None,
                "total": 0,
                "completed": 0,
                "credits_used": 0,
                "pages_stored": 0,
                "requests_count": 0,
                "retry_count": 0,
                "error_type": None,
                "error_message": None,
                "started_at": datetime.now(UTC).isoformat(),
                "finished_at": None,
            },
            "pages": [],
        }
        self._flush()

    async def attach_provider_job(
        self,
        run_id: str,
        provider_job_id: str,
        *,
        requests_count: int,
        retry_count: int,
    ) -> None:
        run = self._run(run_id)
        run.update(
            {
                "provider_job_id": provider_job_id,
                "status": "running",
                "provider_status": "scraping",
                "requests_count": requests_count,
                "retry_count": retry_count,
            }
        )
        self._flush()

    async def save_pages(self, run_id: str, pages: tuple[FirecrawlPage, ...]) -> int:
        self._run(run_id)
        stored = self._document["pages"]
        existing_urls = {page["source_url"] for page in stored}
        inserted = 0
        for page in pages:
            if page.source_url in existing_urls:
                continue
            stored.append(
                {
                    "source_url": page.source_url,
                    "title": page.title,
                    "markdown": page.markdown,
                    "raw_payload": page.raw_payload,
                    "observed_at": page.observed_at.isoformat(),
                }
            )
            existing_urls.add(page.source_url)
            inserted += 1
        if inserted:
            self._flush()
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
        run = self._run(run_id)
        run.update(
            {
                "provider_status": provider_status,
                "total": total,
                "completed": completed,
                "credits_used": credits_used,
                "pages_stored": pages_stored,
                "requests_count": requests_count,
                "retry_count": retry_count,
            }
        )
        self._flush()

    async def mark_succeeded(self, run_id: str) -> FirecrawlSummary:
        run = self._run(run_id)
        run.update(
            {
                "status": "succeeded",
                "provider_status": "completed",
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        self._flush()
        return self._summary(cache_hit=False)

    async def mark_failed(
        self,
        run_id: str,
        *,
        error_type: str,
        error_message: str,
    ) -> None:
        run = self._run(run_id)
        run.update(
            {
                "status": "failed",
                "error_type": error_type,
                "error_message": error_message,
                "finished_at": datetime.now(UTC).isoformat(),
            }
        )
        self._flush()

    def _run(self, run_id: str) -> dict[str, Any]:
        run = self._document.get("run")
        if not isinstance(run, dict) or run.get("id") != run_id:
            raise RuntimeError("Firecrawl JSON run is not initialized")
        return run

    def _summary(self, *, cache_hit: bool) -> FirecrawlSummary:
        run = self._document["run"]
        finished_at = run["finished_at"] or datetime.now(UTC).isoformat()
        return FirecrawlSummary(
            run_id=run["id"],
            provider_job_id=run["provider_job_id"] or "",
            status=run["status"],
            pages_stored=run["pages_stored"],
            total=run["total"],
            completed=run["completed"],
            credits_used=run["credits_used"],
            requests_count=run["requests_count"],
            retry_count=run["retry_count"],
            cache_hit=cache_hit,
            started_at=datetime.fromisoformat(run["started_at"]),
            finished_at=datetime.fromisoformat(finished_at),
        )

    def _flush(self) -> None:
        temporary = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._document, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.output_path)
