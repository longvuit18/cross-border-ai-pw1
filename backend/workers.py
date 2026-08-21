from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.firecrawl_models import firecrawl_location_payload


@dataclass(frozen=True, slots=True)
class CrawlWorker:
    worker_id: str
    query: str
    directory: Path
    created_at: str
    updated_at: str
    status: str

    @property
    def manifest_path(self) -> Path:
        return self.directory / "worker.json"

    @property
    def etsy_path(self) -> Path:
        return self.directory / "etsy.json"

    @property
    def normalized_path(self) -> Path:
        return self.directory / "normalized.json"


class WorkerRepository:
    def __init__(
        self,
        root: str | Path = "data/workers",
        *,
        printway_path: str | Path = "data/printway_catalog.json",
    ) -> None:
        self.root = Path(root)
        self.printway_path = Path(printway_path)

    def create(self, query: str, *, worker_id: str | None = None) -> CrawlWorker:
        clean_query = " ".join(query.split())
        if not clean_query:
            raise ValueError("worker query must not be empty")
        resolved_id = worker_id or worker_id_from_query(clean_query)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", resolved_id):
            raise ValueError("worker_id must contain lowercase letters, numbers, _ or -")
        directory = self.root / resolved_id
        manifest_path = directory / "worker.json"
        if manifest_path.exists():
            raise ValueError(f"worker {resolved_id} already exists")
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 1,
            "worker_id": resolved_id,
            "query": clean_query,
            "status": "created",
            "created_at": timestamp,
            "updated_at": timestamp,
            "crawl_config": {
                "location": firecrawl_location_payload(),
            },
            "data": {
                "etsy_raw": "etsy.json",
                "normalized": "normalized.json",
                "printway_catalog": _relative_path(self.printway_path, directory),
            },
        }
        _atomic_json_write(manifest_path, manifest)
        return self.get(resolved_id)

    def import_existing(
        self,
        query: str,
        source: str | Path,
        *,
        worker_id: str | None = None,
    ) -> CrawlWorker:
        source_path = Path(source)
        if not source_path.exists():
            raise ValueError(f"source JSON does not exist: {source_path}")
        worker = self.create(query, worker_id=worker_id)
        shutil.copy2(source_path, worker.etsy_path)
        self.update_status(worker.worker_id, "enriching")
        return self.get(worker.worker_id)

    def list(self) -> list[CrawlWorker]:
        if not self.root.exists():
            return []
        workers: list[CrawlWorker] = []
        for manifest_path in sorted(self.root.glob("*/worker.json")):
            try:
                workers.append(self._from_manifest(manifest_path))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(workers, key=lambda worker: worker.updated_at, reverse=True)

    def get(self, worker_id: str) -> CrawlWorker:
        manifest_path = self.root / worker_id / "worker.json"
        if not manifest_path.exists():
            raise ValueError(f"worker {worker_id} does not exist")
        return self._from_manifest(manifest_path)

    def update_status(self, worker_id: str, status: str) -> CrawlWorker:
        worker = self.get(worker_id)
        document = json.loads(worker.manifest_path.read_text(encoding="utf-8"))
        document["status"] = status
        document["updated_at"] = datetime.now(UTC).isoformat()
        _atomic_json_write(worker.manifest_path, document)
        return self.get(worker_id)

    def manifest(self, worker_id: str) -> dict[str, Any]:
        worker = self.get(worker_id)
        document = json.loads(worker.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("worker manifest must be a JSON object")
        return document

    @staticmethod
    def _from_manifest(manifest_path: Path) -> CrawlWorker:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        return CrawlWorker(
            worker_id=str(document["worker_id"]),
            query=str(document["query"]),
            directory=manifest_path.parent,
            created_at=str(document["created_at"]),
            updated_at=str(document["updated_at"]),
            status=str(document.get("status") or "unknown"),
        )


def worker_id_from_query(query: str) -> str:
    ascii_query = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_query.lower()).strip("_")
    if len(slug) < 2:
        raise ValueError("query must produce a worker id with at least two characters")
    return slug[:80].rstrip("_")


def _relative_path(path: Path, directory: Path) -> str:
    return os.path.relpath(path, directory)


def _atomic_json_write(path: Path, document: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
