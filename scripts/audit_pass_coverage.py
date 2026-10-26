#!/usr/bin/env python3
"""Audit observed pass cadence; optionally rebuild verified same-pass mask unions."""

import argparse
import json
from pathlib import Path

from puertogt.pass_coverage import pass_coverage_report
from puertogt.pass_masks import build_pass_union_cache
from puertogt.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--ports", type=Path, default=Path("config/ports.geojson"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", help="Inclusive ISO8601 timestamp with timezone")
    parser.add_argument("--end", help="Exclusive ISO8601 timestamp with timezone")
    parser.add_argument("--port-id")
    parser.add_argument("--sensor", choices=("sentinel-1", "sentinel-2"))
    parser.add_argument("--recipe-version")
    parser.add_argument("--min-observable-fraction", type=float, default=0.9)
    parser.add_argument(
        "--rebuild-masks",
        action="store_true",
        help="Verify source checksums and rebuild the local mask-union cache",
    )
    args = parser.parse_args()
    database = args.data_dir / "catalog.sqlite"
    if not database.is_file():
        parser.error(f"catalogue does not exist: {database}")
    store = Store(database, data_dir=args.data_dir)
    cache = args.data_dir / "coverage/pass-unions-v1.json"
    if args.rebuild_masks:
        result = build_pass_union_cache(store, args.ports, cache)
        print(
            json.dumps(
                {
                    "mask_union_entries": len(result["entries"]),
                    "mask_union_errors": len(result["errors"]),
                    "verified_source_bytes": result["verified_source_bytes"],
                }
            )
        )
    report = pass_coverage_report(
        store,
        args.ports,
        port_id=args.port_id,
        sensor=args.sensor,
        start=args.start,
        end=args.end,
        recipe_version=args.recipe_version,
        min_observable_fraction=args.min_observable_fraction,
        mask_union_cache=cache,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), "source": report["source"]}))


if __name__ == "__main__":
    main()
