from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.workers import WorkerRepository, worker_id_from_query


def test_query_becomes_stable_worker_id() -> None:
    assert (
        worker_id_from_query("Custom House Portrait Ornament")
        == "custom_house_portrait_ornament"
    )


def test_workers_own_separate_data_paths(tmp_path: Path) -> None:
    repository = WorkerRepository(
        tmp_path / "workers",
        printway_path=tmp_path / "printway_catalog.json",
    )

    christmas = repository.create("Christmas Ornament")
    portrait = repository.create("Custom House Portrait Ornament")

    assert christmas.directory != portrait.directory
    assert christmas.etsy_path != portrait.etsy_path
    assert christmas.normalized_path != portrait.normalized_path
    assert repository.get(christmas.worker_id).query == "Christmas Ornament"
    assert repository.manifest(christmas.worker_id)["crawl_config"]["location"] == {
        "country": "US",
        "languages": ["en-US"],
    }
    assert {worker.worker_id for worker in repository.list()} == {
        "christmas_ornament",
        "custom_house_portrait_ornament",
    }


def test_import_existing_copies_without_modifying_source(tmp_path: Path) -> None:
    source = tmp_path / "legacy.json"
    payload = {"pages": [{"page_type": "listing_detail", "listing_id": "42"}]}
    source.write_text(json.dumps(payload), encoding="utf-8")
    repository = WorkerRepository(tmp_path / "workers")

    worker = repository.import_existing("Christmas Ornament", source)

    assert json.loads(worker.etsy_path.read_text(encoding="utf-8")) == payload
    assert json.loads(source.read_text(encoding="utf-8")) == payload
    assert repository.manifest(worker.worker_id)["status"] == "enriching"


def test_duplicate_worker_is_rejected(tmp_path: Path) -> None:
    repository = WorkerRepository(tmp_path / "workers")
    repository.create("Christmas Ornament")

    with pytest.raises(ValueError, match="already exists"):
        repository.create("Christmas Ornament")
