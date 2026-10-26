#!/usr/bin/env python3
"""Plan and run bounded historical ingestion; use the same arguments to resume."""

import argparse
import json
from contextlib import nullcontext
from pathlib import Path

from puertogt.cli import read_model
from puertogt.history import (
    HistoryLedger,
    HistoryLimits,
    create_plan,
    create_selected_plan,
    recover_local_publication,
    repair_history_evidence,
    run_history,
)
from puertogt.store import Store, utc_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "plan-selected", "run", "status", "repair-evidence", "recover-local-publication"])
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--max-acquisitions", type=int, default=1)
    parser.add_argument("--sensor", choices=["sentinel-1", "sentinel-2"])
    parser.add_argument("--acquisition", help="Process one exact key already present in the frozen plan")
    parser.add_argument("--priority-file", type=Path, help="Optional acquisition_keys JSON, reread between acquisitions")
    parser.add_argument("--selection-file", type=Path, help="Exact acquisition_keys JSON for plan-selected")
    parser.add_argument("--complete-protected", action="store_true",
                        help="Allow only selected captures protected by --source-plan, preserving their original results")
    parser.add_argument("--source-plan", type=Path, help="Original frozen plan that protects the selected pilot captures")
    parser.add_argument("--download-budget-gib", type=float, default=200)
    parser.add_argument("--acquisition-budget-gib", type=float, default=1)
    parser.add_argument("--storage-budget-gib", type=float, default=100)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--io-workers", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--reuse-connections", action="store_true",
                        help="Reuse HTTPS connections for the same bounded, pinned public byte ranges")
    parser.add_argument("--materialize-only", action="store_true")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--resume-paused", action="store_true")
    parser.add_argument("--recovery-reason", help="Document the verified cause for a targeted local publication recovery")
    args = parser.parse_args()
    if args.threads < 1 or args.max_acquisitions < 1:
        parser.error("Threads and acquisitions must be positive")
    if args.command == "recover-local-publication" and (not args.acquisition or not args.recovery_reason):
        parser.error("recover-local-publication requires --acquisition and --recovery-reason")
    if args.command == "plan-selected" and not args.selection_file:
        parser.error("plan-selected requires --selection-file")
    if args.complete_protected and (args.command != "plan-selected" or not args.source_plan):
        parser.error("--complete-protected requires plan-selected and --source-plan")
    catalog = Store(args.data_dir / "catalog.sqlite", args.data_dir)
    limits = HistoryLimits(total_download_bytes=int(args.download_budget_gib * 1024**3),
                           acquisition_download_bytes=int(args.acquisition_budget_gib * 1024**3),
                           volume_limit_bytes=int(args.storage_budget_gib * 1024**3), io_workers=args.io_workers)
    if args.command != "run" and (args.plan_dir / "limits.json").exists():
        limits = HistoryLimits(**json.loads((args.plan_dir / "limits.json").read_text()))
    ledger = HistoryLedger(args.plan_dir, limits)
    features = json.loads((args.config_dir / "ports.geojson").read_text())["features"]
    if args.command == "plan-selected":
        selected = json.loads(args.selection_file.read_text())
        keys = selected if isinstance(selected, list) else selected["acquisition_keys"]
        plan = create_selected_plan(catalog, features, args.plan_dir, acquisition_keys=keys,
                                    allow_protected_keys=keys if args.complete_protected else [],
                                    protected_source_plan=args.source_plan)
    else:
        plan = create_plan(catalog, features, args.plan_dir)
    ledger.initialize_plan(plan)
    if args.command in {"plan", "plan-selected"}:
        result = {"plan_id": plan["plan_id"], "acquisitions": len(plan["acquisitions"]),
                  "tiles": sum(len(item["tiles"]) for item in plan["acquisitions"]),
                  "zone_observations": sum(len(item["zones"]) for item in plan["acquisitions"]),
                  "protected_observations": len(plan["protected_observation_ids"])}
    elif args.command == "status":
        result = ledger.summary()
    elif args.command == "recover-local-publication":
        audit = recover_local_publication(catalog, plan, ledger, args.acquisition, reason=args.recovery_reason)
        result = {"acquisition_key": audit["acquisition_key"], "recovery_status": audit["status"],
                  "created_at": audit["created_at"], "completed_at": audit["completed_at"],
                  "job_id": audit["job_before"]["job_id"], "attempts_preserved": audit["job_before"]["attempts"]}
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
        from puertogt.ml.public_attribute_transport import RangeSession

        connection_pool = RangeSession(max_connections=args.io_workers) if args.reuse_connections else nullcontext()
        with connection_pool as transport:
            result = run_history(catalog, plan, ledger, models, max_acquisitions=args.max_acquisitions,
                                 sensor=args.sensor, quality_models=args.data_dir / "models" / "omnicloudmask",
                                 materialize_only=args.materialize_only, quality=not args.skip_quality,
                                 keep_work=args.keep_work, acquisition_key=args.acquisition,
                                 priority_file=args.priority_file,
                                 range_opener=transport.open if transport else None)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
