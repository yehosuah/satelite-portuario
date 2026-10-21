"""Explicit local operations; HTTP never starts downloads or model training."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from puertogt.api import config_path
from puertogt.catalog import discover, load_ports
from puertogt.contracts import ModelManifest, Scene, as_utc
from puertogt.export import export_bundle, export_geojson, export_parquet, restore_bundle
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.models import processing_recipe
from puertogt.review import export_dataset
from puertogt.store import Store, json_text


def timestamp(value):
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use an ISO8601 timestamp with UTC offset") from exc


def read_model(path: Path, data_dir: Path) -> ModelManifest:
    model = ModelManifest.model_validate(yaml.safe_load(path.read_text()))
    if model.task == "attributes":
        from puertogt.ml.attributes import load_attribute_manifest

        model = load_attribute_manifest(path)

    def resolve(value):
        if value.get("weights_path"):
            supplied = Path(value["weights_path"])
            candidates = [
                path.parent / supplied,
                supplied,
                data_dir / supplied,
                data_dir / supplied.name,
            ]
            # Repository recipes use data/models/...; a mounted data volume
            # already represents that leading directory.
            if supplied.parts and supplied.parts[0] == "data":
                candidates.append(data_dir.joinpath(*supplied.parts[1:]))
            found = next((p.resolve() for p in candidates if p.is_file()), None)
            if found:
                value["weights_path"] = str(found)
        encoder = value.get("metadata", {}).get("encoder_manifest")
        if encoder:
            resolve(encoder)
        return value

    model = ModelManifest.model_validate(resolve(model.model_dump(mode="json")))
    return model


def runtime_limits() -> RuntimeLimits:
    path = config_path() / "runtime.yaml"
    value = yaml.safe_load(path.read_text()) if path.is_file() else {}
    queue = value.get("queue", {})
    storage = value.get("storage", {})
    return RuntimeLimits(
        **{
            name: queue[name]
            for name in (
                "lease_seconds",
                "retry_base_seconds",
                "retry_max_seconds",
                "pause_seconds",
                "max_attempts",
            )
            if name in queue
        },
        **{
            name: storage[name]
            for name in ("reserve_bytes", "volume_limit_bytes")
            if name in storage
        },
    )


def doctor(store: Store) -> dict:
    with closing(store.connect()) as tx:
        models = [json.loads(row[0]) for row in tx.execute("SELECT data_json FROM models")]
        counts = {
            table: tx.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("acquisitions", "observations", "assets", "models", "revisions")
        }
        states = {
            row[0]: row[1]
            for row in tx.execute("SELECT status,count(*) FROM observations GROUP BY status")
        }
    required = {
        ("detection", "sentinel-1"),
        ("detection", "sentinel-2"),
        ("attributes", "sentinel-1"),
        ("attributes", "sentinel-2"),
        ("change", "sentinel-2"),
    }
    validated = {
        (m["task"], m["sensor"])
        for m in models
        if m.get("publication_policy", {}).get("status") == "validated"
        and m.get("weights_sha256")
        and m.get("dataset_version")
    }
    missing = [
        f"Independent local validation and own weights: {task}/{sensor}"
        for task, sensor in sorted(required - validated)
    ]
    if not counts["revisions"]:
        missing.append("Reviewed local labels and frozen test set")
    if not states.get("usable") and not states.get("partial"):
        missing.append("Processed observable evidence")
    release = store.data_dir / "release-evidence.json"
    evidence = json.loads(release.read_text()) if release.is_file() else {}
    for name in ("history_reconciled", "restore_verified", "pilot_measured"):
        if evidence.get(name) is not True:
            missing.append(name)
    return {
        "software": "operational",
        "counts": counts,
        "observation_states": states,
        "release_ready": not missing,
        "missing_evidence": missing,
        "limits": "Synthetic tests and upstream pilots do not certify local precision.",
    }


def parser():
    root = argparse.ArgumentParser(
        prog="puertogt", description="Observatorio local Sentinel de puertos guatemaltecos"
    )
    root.add_argument("--data-dir", type=Path, default=Path(os.getenv("PUERTOGT_DATA_DIR", "data")))
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create catalog and local volume")
    inspect = commands.add_parser("doctor", help="Inspect service and missing scientific evidence")
    inspect.add_argument("--check-release", action="store_true")
    serve = commands.add_parser("serve", help="Run local query API and review UI")
    serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1", "0.0.0.0"])
    serve.add_argument("--port", type=int, default=8000)
    inv = commands.add_parser("inventory", help="Fetch metadata only, with full pagination")
    inv.add_argument("--start", type=timestamp, default=datetime(2025, 1, 1, tzinfo=timezone.utc))
    inv.add_argument("--end", type=timestamp, default=None)
    inv.add_argument("--port", choices=["prq", "stc", "pbr"], action="append")
    inv.add_argument("--output", type=Path)
    update = commands.add_parser(
        "update", help="Rediscover overlapping recent metadata including late publications"
    )
    update.add_argument("--lookback-days", type=int, default=90)
    update.add_argument("--port", choices=["prq", "stc", "pbr"], action="append")
    ingest = commands.add_parser(
        "import-scenes", help="Import Scene JSON records without processing"
    )
    ingest.add_argument("path", type=Path)
    enqueue = commands.add_parser(
        "enqueue", help="Schedule an explicit scene and zone, including pilot work"
    )
    enqueue.add_argument("scene", type=Path)
    enqueue.add_argument("--zone", required=True)
    enqueue.add_argument("--model", type=Path, required=True)
    enqueue.add_argument("--attributes-model", type=Path)
    enqueue.add_argument(
        "--bundle", type=Path, help="Prepared RasterBundle JSON with numeric paths"
    )
    fill = commands.add_parser(
        "backfill", help="Schedule bounded batches after measured pilot gate"
    )
    fill.add_argument("--model", type=Path, required=True)
    fill.add_argument("--attributes-model", type=Path)
    fill.add_argument("--pilot", type=Path, required=True)
    fill.add_argument("--limit", type=int, default=10)
    worker = commands.add_parser("worker", help="Process queued scenes outside HTTP")
    worker.add_argument("--model", type=Path, required=True)
    worker.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    worker.add_argument("--max-jobs", type=int, default=1)
    worker.add_argument("--attributes-model", type=Path)
    worker.add_argument(
        "--allow-experimental",
        action="store_true",
        help="Explicitly process baseline proposals for review; never certifies quality",
    )
    export = commands.add_parser(
        "export", help="Export consistent portable snapshot or result data"
    )
    export.add_argument("destination", type=Path)
    export.add_argument("--format", choices=["bundle", "geojson", "parquet"], default="bundle")
    export.add_argument("--entity", choices=["detections", "changes"], default="detections")
    restore = commands.add_parser(
        "restore", help="Verify checksums and restore into a clean directory"
    )
    restore.add_argument("bundle", type=Path)
    restore.add_argument("destination", type=Path)
    dataset = commands.add_parser(
        "dataset", help="Freeze complete-scene reviewed labels without split leakage"
    )
    dataset.add_argument("destination", type=Path)
    dataset.add_argument("--split", choices=["train", "calibration", "test"], required=True)
    dataset.add_argument("--version", required=True)
    history = commands.add_parser(
        "history-report", help="Reconcile catalog, observations and all unprocessed acquisitions"
    )
    history.add_argument("destination", type=Path)
    resume = commands.add_parser(
        "resume", help="Resume a resource-paused job after correcting its limit"
    )
    resume.add_argument("job_id")
    cache = commands.add_parser(
        "cache-features", help="Extract pinned frozen-encoder features for reviewed train labels"
    )
    cache.add_argument("dataset", type=Path)
    cache.add_argument("--model", type=Path, required=True)
    for name in ("train-detector", "train-attributes", "train-change"):
        train = commands.add_parser(name, help="Supervised training from a reviewed train split")
        train.add_argument("dataset", type=Path)
        train.add_argument("destination", type=Path)
        train.add_argument("--model", type=Path, required=True)
        train.add_argument("--version", required=True)
        train.add_argument("--epochs", type=int, default=1)
        train.add_argument("--learning-rate", type=float, default=None)
        train.add_argument("--reserved", type=Path, action="append", default=[])
        train.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    for name in ("evaluate-detector", "calibrate-detector"):
        evaluate = commands.add_parser(
            name, help="Independent evaluation or calibration on an isolated split"
        )
        evaluate.add_argument("dataset", type=Path)
        evaluate.add_argument("destination", type=Path)
        evaluate.add_argument("--model", type=Path, required=True)
        evaluate.add_argument("--min-support", type=int, required=True)
        evaluate.add_argument("--device", choices=["cpu", "mps"], default="cpu")
        if name == "evaluate-detector":
            evaluate.add_argument(
                "--stratum",
                action="append",
                required=True,
                help="port|sensor|zone; repeat per required scope",
            )
    attributes_eval = commands.add_parser(
        "evaluate-attributes",
        help="Evaluate trained attribute heads on independent test references",
    )
    attributes_eval.add_argument("dataset", type=Path)
    attributes_eval.add_argument("destination", type=Path)
    attributes_eval.add_argument("--model", type=Path, required=True)
    attributes_eval.add_argument("--family-threshold", type=float)
    change_eval = commands.add_parser(
        "evaluate-change", help="Evaluate frozen human-reviewed optical test pairs"
    )
    change_eval.add_argument("dataset", type=Path)
    change_eval.add_argument("destination", type=Path)
    change_eval.add_argument("--model", type=Path, required=True)
    model_export = commands.add_parser(
        "model", help="Write a registered model manifest for use with its data volume"
    )
    model_export.add_argument("model_id")
    model_export.add_argument("destination", type=Path)
    compare = commands.add_parser(
        "compare-changes", help="Infer and register candidates from comparable optical bundles"
    )
    compare.add_argument("reference", type=Path)
    compare.add_argument("target", type=Path)
    compare.add_argument("--model", type=Path, required=True)
    temporal = commands.add_parser(
        "track-change", help="Audit comparable t2 evidence or a later reversal"
    )
    temporal.add_argument("event_id")
    temporal.add_argument("bundle", type=Path)
    temporal.add_argument("--changed", choices=["true", "false", "unknown"], required=True)
    temporal.add_argument("--author", required=True)
    return root


def execute(args):
    if args.command == "restore":
        restored = restore_bundle(args.bundle, args.destination)
        return {"restored": str(restored.data_dir), "doctor": doctor(restored)}
    store = Store(args.data_dir / "catalog.sqlite", args.data_dir)
    if args.command == "init":
        return {"initialized": str(store.data_dir)}
    if args.command == "resume":
        JobQueue(store, runtime=runtime_limits()).resume(args.job_id)
        return {"resumed": args.job_id}
    if args.command == "model":
        with closing(store.connect()) as tx:
            row = tx.execute(
                "SELECT data_json FROM models WHERE model_id=?", (args.model_id,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown registered model")
        args.destination.parent.mkdir(parents=True, exist_ok=True)
        with args.destination.open("x") as handle:
            handle.write(row[0] + "\n")
        return {"manifest": str(args.destination), "weights_relative_to": str(store.data_dir)}
    if args.command == "doctor":
        return doctor(store)
    if args.command == "serve":
        import uvicorn

        from puertogt.api import create_app

        if args.host == "0.0.0.0" and not os.getenv("PUERTOGT_CONTAINER"):
            raise ValueError(
                "Network exposure requires deployment authentication and transport; use localhost"
            )
        uvicorn.run(create_app(store), host=args.host, port=args.port)
        return None
    if args.command in {"inventory", "update"}:
        end = (
            datetime.now(timezone.utc) if args.command == "update" or args.end is None else args.end
        )
        if args.command == "update":
            if not 1 <= args.lookback_days <= 730:
                raise ValueError(
                    "lookback-days must be 1..730; use inventory for older publications"
                )
            start = end - timedelta(days=args.lookback_days)
        else:
            start = args.start
        keys = set()
        for port in args.port or ["prq", "stc", "pbr"]:
            for scene in discover(port, start, end, ports_path=config_path() / "ports.geojson"):
                keys.add(store.register_scene(scene))
        result = {
            "captured_interval": [start.isoformat(), end.isoformat()],
            "unique_acquisitions": len(keys),
            "processing_started": False,
        }
        if args.command == "inventory" and args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json_text(result) + "\n")
        return result
    if args.command == "import-scenes":
        content = json.loads(args.path.read_text())
        records = (
            content
            if isinstance(content, list)
            else content.get("scenes", content.get("acquisitions", []))
        )
        scenes = [Scene.model_validate(item) for item in records]
        if not scenes:
            raise ValueError("no Scene records; expected an array, scenes or acquisitions field")
        return {"imported": len({store.register_scene(scene) for scene in scenes})}
    if args.command == "enqueue":
        scene = Scene.model_validate_json(args.scene.read_text())
        zones = {
            f["properties"]["zone_id"]: f["properties"]["port_id"]
            for port in load_ports(config_path() / "ports.geojson").values()
            for f in port["zones"]
        }
        if args.zone not in zones:
            raise ValueError("unknown zone")
        model = read_model(args.model, store.data_dir)
        attributes = (
            read_model(args.attributes_model, store.data_dir) if args.attributes_model else None
        )
        if model.sensor != scene.sensor:
            raise ValueError("model and scene sensors differ")
        if args.bundle:
            from puertogt.contracts import RasterBundle

            bundle = RasterBundle.model_validate_json(args.bundle.read_text())
            scene.metadata["prepared_bundle"] = bundle.model_dump(mode="json")
        return {
            "job_id": JobQueue(store, runtime=runtime_limits()).enqueue(
                scene, args.zone, processing_recipe(model, attributes), port_id=zones[args.zone]
            )
        }
    if args.command == "backfill":
        gate = json.loads(args.pilot.read_text())
        if (
            gate.get("bulk_processing_approved") is not True
            or not gate.get("measured_bytes_per_scene")
            or not gate.get("measured_seconds_per_scene")
        ):
            raise ValueError(
                "bulk processing requires successful measured pilot, resource projection and explicit recorded gate"
            )
        if not 1 <= args.limit <= 100:
            raise ValueError("batch limit must be 1..100")
        model = read_model(args.model, store.data_dir)
        attributes = (
            read_model(args.attributes_model, store.data_dir) if args.attributes_model else None
        )
        with closing(store.connect()) as tx:
            rows = tx.execute(
                "SELECT data_json FROM acquisitions WHERE sensor=? AND captured_at>=? ORDER BY captured_at",
                (model.sensor, "2025-01-01"),
            ).fetchall()
        ports = load_ports(config_path() / "ports.geojson")
        queue = JobQueue(store, runtime=runtime_limits())
        job_ids = []
        from shapely.geometry import shape

        for row in rows:
            scene = Scene.model_validate_json(row[0])
            for port_id in scene.port_ids:
                for feature in ports[port_id]["zones"]:
                    zone = feature["properties"]["zone_id"]
                    if not shape(scene.footprint).intersects(shape(feature["geometry"])):
                        continue
                    with closing(store.connect()) as tx:
                        prior = tx.execute(
                            "SELECT 1 FROM observations WHERE acquisition_key=? AND zone_id=? AND recipe_version=?",
                            (scene.acquisition_key, zone, processing_recipe(model, attributes)),
                        ).fetchone()
                    if prior:
                        continue
                    job_ids.append(
                        queue.enqueue(
                            scene,
                            zone,
                            processing_recipe(model, attributes),
                            port_id=port_id,
                            estimated_bytes=int(gate["measured_bytes_per_scene"]),
                        )
                    )
                    if len(job_ids) >= args.limit:
                        return {"enqueued": len(job_ids), "job_ids": job_ids}
        return {"enqueued": len(job_ids), "job_ids": job_ids}
    if args.command == "worker":
        from puertogt.worker import process_once

        if not 1 <= args.max_jobs <= 100:
            raise ValueError("max-jobs must be 1..100")
        model = read_model(args.model, store.data_dir)
        if model.publication_policy.get("status") != "validated" and not args.allow_experimental:
            raise ValueError(
                "Unvalidated model: use --allow-experimental only for reviewed pilot proposals"
            )
        results = []
        attributes = None
        if args.attributes_model:
            attributes = read_model(args.attributes_model, store.data_dir)
        for _ in range(args.max_jobs):
            result = process_once(
                store,
                model,
                device=args.device,
                runtime=runtime_limits(),
                attribute_model=attributes,
            )
            if result is None:
                break
            results.append(result)
        return {"jobs": results}
    if args.command == "cache-features":
        from puertogt.ml.training import cache_reviewed_features
        from puertogt.review import bundle_from_evidence

        result = cache_reviewed_features(
            args.dataset,
            read_model(args.model, store.data_dir),
            lambda sample: bundle_from_evidence(sample, args.dataset),
        )
        return {"manifest": str(result)}
    if args.command in {"train-detector", "train-attributes", "train-change"}:
        from puertogt.ml.change_workflows import train_change_dataset
        from puertogt.ml.training import train_attribute_dataset
        from puertogt.ml.workflows import train_detector
        from puertogt.models import register_portable_model

        base = read_model(args.model, store.data_dir)
        base = base.model_copy(
            update={
                "model_id": f"puertogt-{base.task}-{base.sensor}-{args.version}",
                "version": args.version,
            }
        )
        train = {
            "train-detector": train_detector,
            "train-attributes": train_attribute_dataset,
            "train-change": train_change_dataset,
        }[args.command]
        result = train(
            args.dataset,
            base,
            args.destination,
            epochs=args.epochs,
            learning_rate=args.learning_rate
            or (2e-5 if args.command == "train-detector" else 1e-3),
            device=args.device,
            reserved_manifests=args.reserved,
        )
        artifact = read_model(args.destination / "manifest.json", store.data_dir)
        register_portable_model(store, artifact)
        return {
            "manifest": str(args.destination / "manifest.json"),
            "model_id": result.model_id,
            "validation_status": "experimental",
            "weights_sha256": result.weights_sha256,
        }
    if args.command in {"evaluate-detector", "calibrate-detector"}:
        from puertogt.ml.workflows import calibrate_detector, evaluate_detector, save_report

        model = read_model(args.model, store.data_dir)
        if args.command == "evaluate-detector":
            report = evaluate_detector(
                args.dataset,
                model,
                required_strata=args.stratum,
                min_support=args.min_support,
                device=args.device,
            )
        else:
            report = calibrate_detector(
                args.dataset, model, min_support=args.min_support, device=args.device
            )
        save_report(report, args.destination)
        return report
    if args.command == "evaluate-attributes":
        from puertogt.ml.attribute_workflows import evaluate_attributes
        from puertogt.ml.workflows import save_report

        report = evaluate_attributes(
            args.dataset,
            read_model(args.model, store.data_dir),
            family_threshold=args.family_threshold,
        )
        save_report(report, args.destination)
        return report
    if args.command == "evaluate-change":
        from puertogt.ml.change_evaluation import evaluate_change_dataset
        from puertogt.ml.workflows import save_report

        report = evaluate_change_dataset(args.dataset, read_model(args.model, store.data_dir))
        save_report(report, args.destination)
        return report
    if args.command in {"compare-changes", "track-change"}:
        from puertogt.contracts import RasterBundle
        from puertogt.ml.change_workflows import compare_and_register, record_temporal_evidence
        from puertogt.worker import resolve_bundle_paths

        def bundle_from_file(path):
            value = RasterBundle.model_validate_json(path.read_text())
            return resolve_bundle_paths(value, store.data_dir, relative_to=path.parent)

        if args.command == "compare-changes":
            events = compare_and_register(
                store,
                bundle_from_file(args.reference),
                bundle_from_file(args.target),
                read_model(args.model, store.data_dir),
            )
            return {"events": [event.model_dump(mode="json") for event in events]}
        changed = {"true": True, "false": False, "unknown": None}[args.changed]
        return record_temporal_evidence(
            store, args.event_id, bundle_from_file(args.bundle), changed=changed, author=args.author
        ).model_dump(mode="json")
    if args.command == "export":
        if args.format == "bundle":
            safe_config = {
                p.name: yaml.safe_load(p.read_text())
                if p.suffix == ".yaml"
                else json.loads(p.read_text())
                for p in [config_path() / "runtime.yaml", config_path() / "ports.geojson"]
                if p.is_file()
            }
            return export_bundle(store, args.destination, config=safe_config).model_dump(
                mode="json"
            )
        result = (
            export_geojson(store, args.destination, entity=args.entity)
            if args.format == "geojson"
            else export_parquet(store, args.destination, entity=args.entity)
        )
        return {"path": str(result)}
    if args.command == "dataset":
        return export_dataset(store, args.destination, split=args.split, version=args.version)
    if args.command == "history-report":
        import pyarrow as pa
        import pyarrow.parquet as pq

        from puertogt.coverage import coverage_report

        args.destination.mkdir(parents=True, exist_ok=True)
        reconciliation = coverage_report(store, config_path() / "ports.geojson")
        coverage = reconciliation["items"]
        with closing(store.connect()) as tx:
            missing = [
                dict(r)
                for r in tx.execute(
                    "SELECT acquisition_key,sensor,captured_at FROM acquisitions WHERE captured_at>='2025-01-01' AND acquisition_key NOT IN (SELECT acquisition_key FROM observations)"
                )
            ]
        for item in missing:
            item["reason"] = "not_scheduled_before_pilot_gate"
        exceptions = JobQueue(store).exceptions()
        # Keep provider messages local; public reports contain reason codes only.
        for item in exceptions:
            item.pop("error_message", None)
        pq.write_table(
            pa.Table.from_pylist(coverage)
            if coverage
            else pa.table({"catalogued": pa.array([], type=pa.int64())}),
            args.destination / "coverage.parquet",
        )
        report = {
            "cutoff_utc": datetime.now(timezone.utc).isoformat(),
            "acquisition_zone_exceptions": reconciliation["exceptions"],
            "not_scheduled": missing,
            "job_exceptions": exceptions,
            "coverage_rows": len(coverage),
        }
        (args.destination / "exceptions.json").write_text(json_text(report) + "\n")
        return {
            "coverage_rows": len(coverage),
            "unscheduled_acquisitions": len(missing),
            "job_exceptions": len(exceptions),
        }
    raise ValueError("unknown command")


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "doctor" and args.check_release and not result["release_ready"]:
            return 2
        return 0
    except (ValueError, FileNotFoundError, FileExistsError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
