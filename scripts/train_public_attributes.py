#!/usr/bin/env python3
"""Run the prespecified public attribute experiment after its inputs are ready.

This command does not pause or resume the historical campaign. It requires its
explicit pause marker and both locks, and never grants publication permission.
"""

import argparse
import json
import shutil
import signal
import time
from pathlib import Path

from puertogt.cli import read_model
from puertogt.history import _atomic_json, file_lock
from puertogt.ml.public_attributes import load_selection, validate_performance_amendment
from puertogt.store import Store, sha256_file, utc_text


def check_pins(repository: Path, plan_path: Path, selection_path: Path, *,
               performance_amendment=None, amendment_sha256=None) -> dict:
    plan = json.loads(plan_path.read_text())
    if plan.get("schema_version") != "public-attribute-experiment-v1":
        raise ValueError("A prespecified public attribute experiment is required")
    load_selection(selection_path, plan["selection_sha256"])
    amended_sources = {}
    if performance_amendment is not None:
        amendment = validate_performance_amendment(performance_amendment, amendment_sha256,
                                                   plan["selection_sha256"])
        if (amendment.get("experiment_plan_sha256") != sha256_file(plan_path)
                or set(amendment.get("source_changes", {})) != {"src/puertogt/ml/public_attributes.py"}):
            raise ValueError("Performance amendment cannot replace model or preprocessing sources")
        for name, change in amendment["source_changes"].items():
            if change["before"] != plan["source_sha256"][name]:
                raise ValueError("Performance amendment has a different source parent")
            amended_sources[name] = change["after"]
    for name, digest in plan["source_sha256"].items():
        if sha256_file(repository / name) != amended_sources.get(name, digest):
            raise ValueError(f"Prespecified source changed: {name}")
    if (plan["training"] != {"epochs": 50, "learning_rate": 0.001, "batch_size": 16,
                            "seed": 0, "device": "cpu", "cpu_threads": 4,
                            "hyperparameter_search": False}
            or any(plan["publication"].values())):
        raise ValueError("The frozen experiment requires its bounded recipe and closed publication gates")
    return plan


def check_numeric_report(repository: Path, dataset: Path, report_path: Path, report_sha256: str,
                         amendment_path: Path, amendment_sha256: str, plan: dict, plan_sha256: str) -> dict:
    from puertogt.ml.public_attribute_eligibility import audit_numeric_datasets

    if sha256_file(report_path) != report_sha256 or sha256_file(amendment_path) != amendment_sha256:
        raise ValueError("Numeric eligibility checksum mismatch")
    report = json.loads(report_path.read_text())
    amendment = json.loads(amendment_path.read_text())
    if (amendment.get("schema_version") != "public-attribute-numeric-eligibility-amendment-v1"
            or amendment.get("selection_sha256") != plan["selection_sha256"]
            or amendment.get("experiment_plan_sha256") != plan_sha256
            or amendment.get("training_started") is not False
            or amendment.get("test_evaluation_started") is not False):
        raise ValueError("Numeric eligibility amendment differs from the original experiment")
    if (report.get("schema_version") != "public-attribute-numeric-eligibility-report-v1"
            or report.get("amendment_sha256") != amendment_sha256
            or report.get("experiment_plan_sha256") != plan_sha256
            or report.get("training_started") is not False
            or report.get("test_evaluation_started") is not False
            or report.get("targets_used_for_eligibility") is not False):
        raise ValueError("A complete numeric eligibility audit must precede model fitting")
    expected_sources = {"src/puertogt/ml/public_attribute_eligibility.py", "scripts/train_public_attributes.py",
                        "scripts/audit_public_attribute_eligibility.py"}
    if set(report.get("source_sha256", {})) != expected_sources:
        raise ValueError("Numeric eligibility sources must be pinned")
    for name, digest in report["source_sha256"].items():
        if sha256_file(repository / name) != digest:
            raise ValueError("Numeric eligibility source changed after audit freeze")
    actual = audit_numeric_datasets(dataset / "datasets", plan["selection_sha256"])
    if any(report.get(key) != value for key, value in actual.items()):
        raise ValueError("Numeric eligibility evidence or decisions changed after freeze")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("reports/public-attributes/experiment-plan-v1.json"))
    parser.add_argument("--selection", type=Path, default=Path("reports/public-attributes/selection-v1.json"))
    parser.add_argument("--performance-amendment", type=Path)
    parser.add_argument("--amendment-sha256")
    parser.add_argument("--numeric-amendment", type=Path, required=True)
    parser.add_argument("--numeric-amendment-sha256", required=True)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--eligibility-report-sha256", required=True)
    parser.add_argument("--dataset-root", type=Path, default=Path("data/public-attributes/v1"))
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--history-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 900:
        raise ValueError("The complete attribute experiment has a maximum 900-second budget")
    repository = Path(__file__).resolve().parents[1]
    plan = check_pins(repository, args.plan, args.selection,
                      performance_amendment=args.performance_amendment, amendment_sha256=args.amendment_sha256)
    if not (args.history_dir / "PAUSE").is_file():
        raise ValueError("Request and confirm the historical acquisition boundary first")
    target = args.destination.resolve()
    dataset = args.dataset_root.resolve()
    if target.exists():
        raise FileExistsError("Experiment attempts are immutable; inspect the existing receipt")
    # Cache construction has its own heavy lock; the campaign lock prevents a
    # historical restart while that operation and the following fit take place.
    with file_lock(args.history_dir / "campaign.lock"):
        from puertogt.ml.public_attribute_eligibility import (
            copy_eligible_dataset,
            evaluate_with_numeric_abstentions,
        )
        from puertogt.ml.public_attributes import cache_public_features
        from puertogt.ml.training import validate_training_dataset

        train = target / "training-features"
        reserved = [dataset / "datasets" / split / "manifest.json" for split in ("calibration", "test")]
        for split in ("train", "calibration", "test"):
            manifest = json.loads((dataset / "datasets" / split / "manifest.json").read_text())
            if manifest.get("selection_sha256") != plan["selection_sha256"]:
                raise ValueError("Dataset selection differs from the frozen experiment")
        validate_training_dataset(dataset / "datasets/train", reserved_manifests=reserved)
        eligibility = check_numeric_report(repository, dataset, args.eligibility_report,
            args.eligibility_report_sha256, args.numeric_amendment, args.numeric_amendment_sha256,
            plan, sha256_file(args.plan))
        encoder = read_model(repository / "config/models/s2.yaml", args.data_dir)
        if encoder.weights_sha256 != plan["encoder_weights_sha256"]:
            raise ValueError("Encoder checkpoint differs from the frozen experiment")
        target.mkdir(parents=True)
        started = time.monotonic()
        status = {"status": "started", "created_at": utc_text(), "plan_sha256": sha256_file(args.plan),
                  "selection_sha256": plan["selection_sha256"], "wall_time_limit_seconds": args.seconds,
                  "cpu_threads": 4, "test_evaluation_started": False, "automatic_publication": False,
                  "performance_amendment_sha256": args.amendment_sha256,
                  "numeric_amendment_sha256": args.numeric_amendment_sha256,
                  "eligibility_report_sha256": args.eligibility_report_sha256,
                  "numeric_eligibility": eligibility["summary"]}
        _atomic_json(target / "status.json", status)
        snapshot = target / "source-replay"
        shutil.copytree(repository / "src", snapshot / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(repository / "config", snapshot / "config")
        (snapshot / "scripts").mkdir()
        for name in (Path(__file__).name, "audit_public_attribute_eligibility.py"):
            shutil.copyfile(repository / "scripts" / name, snapshot / "scripts" / name)
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copyfile(repository / name, snapshot / name)
        for path in [args.plan, args.selection, args.numeric_amendment, args.eligibility_report]:
            shutil.copyfile(path, target / path.name)
        if args.performance_amendment:
            shutil.copyfile(args.performance_amendment, target / args.performance_amendment.name)
        frozen = {str(path.relative_to(snapshot)): sha256_file(path) for path in snapshot.rglob("*") if path.is_file()}
        _atomic_json(target / "source-sha256.json", frozen)

        def deadline(*unused):
            raise TimeoutError("Public attribute experiment reached its fixed wall-time budget")

        previous_handler = signal.signal(signal.SIGALRM, deadline)
        signal.setitimer(signal.ITIMER_REAL, args.seconds)
        try:
            eligible_train = copy_eligible_dataset(dataset / "datasets/train", target / "eligible/train",
                                                   eligibility, args.eligibility_report_sha256)
            eligible_test = copy_eligible_dataset(dataset / "datasets/test", target / "eligible/test",
                                                  eligibility, args.eligibility_report_sha256)
            if not train.exists():
                cache_public_features(eligible_train, encoder, train,
                                      heavy_lock=args.data_dir / ".heavy-worker.lock", threads=4)
            with file_lock(args.data_dir / ".heavy-worker.lock"):
                import torch

                from puertogt.ml.training import train_attribute_dataset
                from puertogt.models import register_portable_model

                torch.set_num_threads(4)
                base = read_model(repository / "config/models/attributes-s2.yaml", args.data_dir)
                if base.metadata.get("encoder_manifest", {}).get("weights_sha256") != encoder.weights_sha256:
                    raise ValueError("Attribute recipe embeds a different encoder checkpoint")
                base = base.model_copy(update={"metadata": {**base.metadata,
                    "feature_context": plan["feature_context"], "public_experiment_plan_sha256": status["plan_sha256"],
                    "performance_amendment_sha256": args.amendment_sha256,
                    "numeric_eligibility_report_sha256": args.eligibility_report_sha256,
                    "numeric_eligibility": eligibility["summary"],
                    "source_scope": "public_attribute_contexts; not_local_validation",
                    "identity_disjointness": "unknown; upstream MMSI absent", "pretraining_overlap": "unknown"}})
                _atomic_json(target / "base-recipe.json", base.model_dump(mode="json"))
                training = plan["training"]
                fitted = train_attribute_dataset(train, base, target / "trained", epochs=training["epochs"],
                    learning_rate=training["learning_rate"], batch_size=training["batch_size"],
                    seed=training["seed"], device="cpu", reserved_manifests=reserved)
                status.update(status="trained", weights_sha256=fitted.weights_sha256,
                              training_manifest_sha256=sha256_file(train / "manifest.json"))
                _atomic_json(target / "status.json", status)
                fitted = read_model(target / "trained/manifest.json", args.data_dir)
                portable = register_portable_model(Store(target / "portable/catalog.sqlite", target / "portable"), fitted)
                _atomic_json(target / "portable/manifest.json", portable.model_dump(mode="json"))
                status.update(status="evaluating_test", test_evaluation_started=True,
                              test_manifest_sha256=sha256_file(reserved[1]))
                _atomic_json(target / "status.json", status)
                result = evaluate_with_numeric_abstentions(dataset / "datasets/test", eligible_test, fitted, eligibility)
                result.update(reference_scope="public_attribute_contexts; not_local_validation",
                              identity_disjointness="unknown; upstream MMSI absent", pretraining_overlap="unknown",
                              calibration_use="reserved; no thresholds selected", trained_model_frozen_before_test=True,
                              test_family_support_limitations="No pasajeros or pesquero examples; seven objects total")
                _atomic_json(target / "public-test-evaluation.json", result)
                status.update(status="complete", evaluation_sha256=sha256_file(target / "public-test-evaluation.json"),
                              completed_at=utc_text(), seconds=time.monotonic() - started)
                _atomic_json(target / "status.json", status)
        except BaseException as error:
            status.update(status="failed", error_type=type(error).__name__, seconds=time.monotonic() - started)
            _atomic_json(target / "status.json", status)
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
