#!/usr/bin/env python3
"""Discover, review and resume a bounded incremental catalogue refresh."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from puertogt.history import HistoryLimits
from puertogt.refresh import _run_path, execute_refresh, freshness_report, plan_refresh
from puertogt.store import Store


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("dry-run", "plan", "run", "refresh", "status"):
        command = commands.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=Path("data"))
        command.add_argument("--config-dir", type=Path, default=Path("config"))
        command.add_argument("--run-id", required=name != "status")
        command.add_argument("--output", type=Path, help="Optional sanitized public receipt copy")
        if name in {"dry-run", "plan", "refresh"}:
            command.add_argument("--lookback-days", type=int, default=90)
            command.add_argument(
                "--end",
                type=datetime.fromisoformat,
                help="Exclusive aware UTC capture cutoff; defaults to the first invocation's current time",
            )
            command.add_argument("--port", action="append", choices=["prq", "pbr", "stc"])
            command.add_argument("--sensor", action="append", choices=["sentinel-1", "sentinel-2"])
            command.add_argument("--max-acquisitions", type=int, default=5)
            command.add_argument("--download-budget-gib", type=float, default=2)
            command.add_argument("--acquisition-budget-gib", type=float, default=0.5)
            command.add_argument("--storage-budget-gib", type=float, default=100)
            command.add_argument("--reserve-gib", type=float, default=5)
            command.add_argument("--threads", type=int, choices=range(1, 5), default=4)
            command.add_argument("--io-workers", type=int, choices=range(1, 5), default=4)
        if name in {"run", "refresh"}:
            command.add_argument(
                "--resume-paused",
                action="store_true",
                help="Retry paused work after fixing disk/resource conditions; never changes frozen budgets",
            )
        if name == "status":
            command.add_argument(
                "--preview",
                action="store_true",
                help="Read a dry-run receipt instead of a processing run",
            )
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        store = Store(args.data_dir / "catalog.sqlite", args.data_dir)
        ports_path = args.config_dir / "ports.geojson"
        if args.command == "status":
            result = {"freshness": freshness_report(store)}
            if args.run_id:
                result["receipt"] = json.loads(
                    (
                        _run_path(store, args.run_id, preview=args.preview) / "receipt.json"
                    ).read_text()
                )
        elif args.command == "run":
            result = execute_refresh(
                store,
                ports_path,
                run_id=args.run_id,
                config_dir=args.config_dir,
                resume_paused=args.resume_paused,
            )
        else:
            limits = HistoryLimits(
                total_download_bytes=int(args.download_budget_gib * 1024**3),
                acquisition_download_bytes=int(args.acquisition_budget_gib * 1024**3),
                volume_limit_bytes=int(args.storage_budget_gib * 1024**3),
                reserve_bytes=int(args.reserve_gib * 1024**3),
                io_workers=args.io_workers,
            )
            result = plan_refresh(
                store,
                ports_path,
                run_id=args.run_id,
                lookback_days=args.lookback_days,
                end=args.end,
                max_acquisitions=args.max_acquisitions,
                limits=limits,
                threads=args.threads,
                port_ids=args.port,
                sensors=args.sensor,
                dry_run=args.command == "dry-run",
            )
            if args.command == "refresh" and result["status"] != "discovery_incomplete":
                result = execute_refresh(
                    store,
                    ports_path,
                    run_id=args.run_id,
                    config_dir=args.config_dir,
                    resume_paused=args.resume_paused,
                )
        code = (
            0
            if result.get("status")
            in {None, "preview", "planned", "no_new_acquisitions", "complete"}
            else 2
        )
    except Exception as exc:
        # Public output contains no source URLs, arbitrary exception text or local paths.
        result, code = {"status": "error", "error_code": type(exc).__name__}, 2
    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    print(serialized, end="", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
