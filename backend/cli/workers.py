from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from backend.workers import WorkerRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage query-scoped crawl workers")
    parser.add_argument("--root", default="data/workers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("query")
    create.add_argument("--worker-id")
    import_existing = subparsers.add_parser("import")
    import_existing.add_argument("query")
    import_existing.add_argument("source")
    import_existing.add_argument("--worker-id")
    subparsers.add_parser("list")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repository = WorkerRepository(args.root)
    try:
        if args.command == "create":
            result = asdict(repository.create(args.query, worker_id=args.worker_id))
        elif args.command == "import":
            result = asdict(
                repository.import_existing(
                    args.query,
                    args.source,
                    worker_id=args.worker_id,
                )
            )
        else:
            result = [asdict(worker) for worker in repository.list()]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
