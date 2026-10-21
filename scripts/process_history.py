#!/usr/bin/env python3
"""Plan and run bounded historical ingestion; use the same arguments to resume."""

import argparse
import json
from pathlib import Path

from puertogt.cli import read_model
from puertogt.history import (
    HistoryLedger,
    HistoryLimits,
    create_plan,
    repair_history_evidence,
    run_history,
)
from puertogt.store import Store, utc_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "status", "repair-evidence"])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--max-acquisitions", type=int, default=1)
    parser.add_argument("--sensor", choices=["sentinel-1", "sentinel-2"])
    parser.add_argument("--acquisition", help="Process one exact key already present in the frozen plan")
    parser.add_argument("--priority-file", type=Path, help="Optional acquisition_keys JSON, reread between acquisitions")
    parser.add_argument("--download-budget-gib", type=float, default=200)
    parser.add_argument("--acquisition-budget-gib", type=float, default=1)
    parser.add_argument("--storage-budget-gib", type=float, default=100)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--io-workers", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--materialize-only", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--resume-paused", action="store_true")
    args = parser.parse_args()
    if args.threads < 1 or args.max_acquisitions < 1:
        parser.error("Threads and acquisitions must be positive")
    catalog = Store(args.data_dir / "catalog.sqlite", args.data_dir)
    limits = HistoryLimits(total_download_bytes=int(args.download_budget_gib * 1024**3),
                           acquisition_download_bytes=int(args.acquisition_budget_gib * 1024**3),
                           volume_limit_bytes=int(args.storage_budget_gib * 1024**3), io_workers=args.io_workers)
    if args.command != "run" and (args.plan_dir / "limits.json").exists():
        limits = HistoryLimits(**json.loads((args.plan_dir / "limits.json").read_text()))
    ledger = HistoryLedger(args.plan_dir, limits)
    plan = create_plan(catalog, json.loads((args.config_dir / "ports.geojson").read_text())["features"],
                       args.plan_dir)
    ledger.initialize_plan(plan)
    if args.command == "plan":
        result = {"plan_id": plan["plan_id"], "acquisitions": len(plan["acquisitions"]),
                  "tiles": sum(len(item["tiles"]) for item in plan["acquisitions"]),
                  "zone_observations": sum(len(item["zones"]) for item in plan["acquisitions"]),
                  "protected_observations": len(plan["protected_observation_ids"])}
    elif args.command == "status":
        result = ledger.summary()
    else:
        import torch
        torch.set_num_threads(args.threads)
        if args.command == "repair-evidence":
            if not args.priority_file:
                parser.error("repair-evidence requires a fixed --priority-file")
            supplied = json.loads(args.priority_file.read_text())
            result = repair_history_evidence(
                catalog, plan, ledger, priority_keys=supplied if isinstance(supplied, list) else supplied["acquisition_keys"],
                quality_models=args.data_dir / "models" / "omnicloudmask")
            print(json.dumps(result, indent=2), flush=True)
            return
        if args.resume_paused:
            with ledger.store.transaction() as tx:
                tx.execute("UPDATE history_acquisitions SET status='retry',not_before=? WHERE status='paused'", (utc_text(),))
        models = {sensor: read_model(args.config_dir / "models" / filename, args.data_dir)
                  for sensor, filename in [("sentinel-1", "s1.yaml"), ("sentinel-2", "s2.yaml")]}
        result = run_history(catalog, plan, ledger, models, max_acquisitions=args.max_acquisitions,
                             sensor=args.sensor, quality_models=args.data_dir / "models" / "omnicloudmask",
                             materialize_only=args.materialize_only, quality=not args.skip_quality,
                             keep_work=args.keep_work, acquisition_key=args.acquisition,
                             priority_file=args.priority_file)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
