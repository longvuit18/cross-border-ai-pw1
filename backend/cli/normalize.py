from __future__ import annotations

import argparse
import json
import sys

from backend.normalization import (
    build_normalized_document,
    load_json,
    write_normalized_document,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Normalize Printway and Etsy JSON")
    parser.add_argument("--printway", default="data/printway_catalog.json")
    parser.add_argument("--etsy", default="data/etsy_christmas_ornament.json")
    parser.add_argument("--output", default="data/normalized_catalog.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        document = build_normalized_document(
            load_json(args.printway),
            load_json(args.etsy),
        )
        write_normalized_document(document, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "succeeded",
                "output": args.output,
                **document["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
