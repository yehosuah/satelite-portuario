"""Freeze and run one TRAIN-only refit after a successful fixed anchor comparison."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import resource
import shutil
import sys
import time
from pathlib import Path


def remaining_budget(comparison_seconds):
    if not math.isfinite(comparison_seconds) or comparison_seconds < 0:
        raise ValueError("Invalid recorded comparison duration")
    remaining = 1200.0 - comparison_seconds
    if remaining <= 180:
        raise ValueError("Insufficient shared budget after reserving 180 seconds for fit and IO")
    return {"total_heavy_seconds": 1200, "comparison_heavy_seconds": comparison_seconds,
            "refit_heavy_seconds": remaining, "training_ceiling_seconds": remaining - 180,
            "fit_and_io_reserved_seconds": 180}


def runtime(source):
    if not os.environ.get("PUERTOGT_WORKSPACE"):
        raise ValueError("PUERTOGT_WORKSPACE must identify the original workspace and shared heavy lock")
    sys.path.insert(0, str(source / "src"))
    sys.path.insert(0, str(source / "scripts"))
    return importlib.import_module("train_anchor_comparison")


def read(path):
    return json.loads(path.read_text())


def eligible_selection(completed, select_recipe):
    if (completed.get("status") != "completed_internal_train_development_only"
            or completed.get("calibration_or_test_opened") is not False
            or completed.get("refit") is not None):
        raise ValueError("A completed TRAIN-only comparison without prior refit is required")
    declared = completed["decision"]
    computed = select_recipe(declared["candidates"])
    if declared != computed or not computed["selected"]:
        raise ValueError("The fixed F1 gain and recall gate did not select a recipe")
    selected = computed["selected"]
    if (selected["name"] != "small_anchors" or selected["anchor_sizes"] != [[8], [16], [32], [64]]
            or selected["epochs"] != 12 or selected["learning_rate"] != .0001
            or selected["sample_order"] != "shuffle"):
        raise ValueError("Unexpected selected recipe")
    return {key: selected[key] for key in ("name", "anchor_sizes", "epochs", "learning_rate", "sample_order")}


def comparison_evidence(comparison, runner):
    plan, base, receipt = runner.verify_frozen(comparison)
    completed = read(comparison / "completed.json")
    selected = eligible_selection(completed, runner.select_recipe)
    if (completed["freeze_sha256"] != runner.sha256_file(comparison / "freeze.json")
            or completed["plan_sha256"] != receipt["plan_sha256"]
            or read(comparison / "selection.json") != completed["decision"]):
        raise ValueError("Comparison completion does not match its freeze and selection")
    paths = [comparison / name for name in ("completed.json", "selection.json", "freeze.json", "plan.json")]
    for candidate in completed["decision"]["candidates"]:
        folder = comparison / candidate["name"]
        model = runner.read_model(folder / "trained/manifest.json", runner.ROOT / "data")
        metrics = read(folder / "development/report.json")["reports_at_fixed_thresholds"][1]
        if (model.weights_sha256 != candidate["weights_sha256"]
                or runner.detector_anchor_sizes(model) != candidate["anchor_sizes"]
                or metrics["overall"] != candidate["overall"] or metrics["ports"] != candidate["ports"]
                or read(folder / "completed.json")["weights_sha256"] != candidate["weights_sha256"]):
            raise ValueError("Comparison artifacts disagree with recorded results")
        paths.extend(folder / name for name in ("completed.json", "trained/manifest.json",
                                                "development/report.json", "development/predictions.json"))
    pins = {str(path.relative_to(runner.ROOT)): runner.sha256_file(path) for path in paths}
    return plan, base, completed, selected, pins


def freeze(comparison, output):
    if output.exists():
        raise FileExistsError("Refit plans and attempts are immutable")
    runner = runtime(comparison / "source")
    parent, _, completed, selected, pins = comparison_evidence(comparison, runner)
    dataset = parent["refit"]
    crops = runner._train_manifest(runner.ROOT / dataset["path"])
    if len(crops["samples"]) != 84 or dataset["split"] != "train":
        raise ValueError("Refit must preserve the original 84 TRAIN crops")
    budget = remaining_budget(completed["heavy_seconds"])
    plan = {"schema_version": "selected-anchor-refit-v1", "created_at": runner.utc_text(),
            "comparison": str(comparison.relative_to(runner.ROOT)), "comparison_evidence": pins,
            "selected_recipe": selected, "dataset": dataset, "planned_steps": 1008,
            "seed": 20261021, "cpu_threads": 4, "encoder_frozen": True,
            "budget": budget, "fit_score_threshold": .5, "fit_iou_threshold": .5,
            "new_march_labels_included": False, "calibration_or_test_permitted": False,
            "automatic_publication": False, "local_validation_demonstrated": False,
            "interpretation": "Refit from the same public initialization on the original 51 TRAIN references. Fit is not independent validation. February has already been used for development and failed CAL remains exposed diagnostic only."}
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(comparison / "source", output / "source", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(Path(__file__), output / "source/scripts/refit_anchor_selection.py")
    runner.write_json(output / "plan.json", plan)
    sources = {str(path.relative_to(output / "source")): runner.sha256_file(path)
               for path in (output / "source").rglob("*") if path.is_file()}
    runner.write_json(output / "freeze.json", {"created_at": runner.utc_text(),
        "plan_sha256": runner.sha256_file(output / "plan.json"), "archived_sources": sources,
        "runtime": runner.runtime_identity(), "source_sha256": runner.source_hashes(),
        "comparison_evidence": pins})
    return {"frozen": True, "plan_sha256": runner.sha256_file(output / "plan.json"),
            "freeze_sha256": runner.sha256_file(output / "freeze.json"), "budget": budget}


def verify(output):
    runner = runtime(output / "source")
    receipt, plan = read(output / "freeze.json"), read(output / "plan.json")
    if (runner.sha256_file(output / "plan.json") != receipt["plan_sha256"]
            or runner.runtime_identity() != receipt["runtime"]
            or runner.source_hashes() != receipt["source_sha256"]):
        raise ValueError("Refit plan, runtime or source fingerprint changed")
    for relative, digest in receipt["archived_sources"].items():
        if runner.sha256_file(output / "source" / relative) != digest:
            raise ValueError("Archived refit source changed")
    if runner.sha256_file(Path(__file__)) != receipt["archived_sources"]["scripts/refit_anchor_selection.py"]:
        raise ValueError("Run the archived refit script")
    for relative, digest in receipt["comparison_evidence"].items():
        if runner.sha256_file(runner.ROOT / relative) != digest:
            raise ValueError("Parent comparison evidence changed")
    parent, base, completed, selected, pins = comparison_evidence(runner.ROOT / plan["comparison"], runner)
    if (plan["selected_recipe"] != selected or plan["dataset"] != parent["refit"]
            or plan["budget"] != remaining_budget(completed["heavy_seconds"])
            or plan["comparison_evidence"] != pins or plan["planned_steps"] != 1008
            or plan["calibration_or_test_permitted"] is not False
            or plan["new_march_labels_included"] is not False):
        raise ValueError("Refit differs from the selected fixed recipe or original TRAIN")
    return runner, plan, base, receipt


def run(output):
    if any((output / name).exists() for name in ("started.json", "completed.json", "failed.json")):
        raise FileExistsError("A frozen refit attempt cannot be repeated")
    runner, plan, base, receipt = verify(output)
    runner.write_json(output / "started.json", {"started_at": runner.utc_text(),
        "freeze_sha256": runner.sha256_file(output / "freeze.json")})
    started = time.monotonic()
    try:
        if shutil.disk_usage(runner.ROOT).free < 5 * 1024**3:
            raise OSError("Insufficient disk reserve")
        selected = plan["selected_recipe"]
        model = runner.configured_base(base, selected, read(runner.ROOT / plan["comparison"] / "freeze.json")["plan_sha256"])
        model = model.model_copy(update={"metadata": {**model.metadata,
                                                      "anchor_refit_plan_sha256": receipt["plan_sha256"]}})
        with runner._heavy_budget(plan["budget"]["training_ceiling_seconds"]):
            runner._seed()
            runner.train_detector(runner.ROOT / plan["dataset"]["path"], model, output / "trained",
                                  epochs=12, learning_rate=.0001, sample_order="shuffle", seed=20261021)
        training_seconds = time.monotonic() - started
        remaining = plan["budget"]["refit_heavy_seconds"] - training_seconds
        with runner._heavy_budget(remaining):
            trained = runner.read_model(output / "trained/manifest.json", runner.ROOT / "data")
            if (runner.detector_anchor_sizes(trained) != selected["anchor_sizes"]
                    or len(trained.metadata["training"]["steps"]) != plan["planned_steps"]):
                raise ValueError("Refit weights lost their recipe or training steps")
            report = runner._measure(runner.ROOT / plan["dataset"]["source_path"], trained, output / "training-fit")
        elapsed = time.monotonic() - started
        result = {"status": "completed_train_refit_only", "plan_sha256": receipt["plan_sha256"],
                  "freeze_sha256": runner.sha256_file(output / "freeze.json"), "selected_recipe": selected,
                  "weights_sha256": trained.weights_sha256, "planned_steps": plan["planned_steps"],
                  "refit_heavy_seconds": elapsed,
                  "aggregate_comparison_and_refit_seconds": plan["budget"]["comparison_heavy_seconds"] + elapsed,
                  "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                  "training_fit": report, "calibration_or_test_opened": False,
                  "local_validation_demonstrated": False, "automatic_publication": False}
        runner.write_json(output / "completed.json", result)
        return result
    except BaseException as exc:
        runner.write_json(output / "failed.json", {"error_type": type(exc).__name__, "message": str(exc),
            "elapsed_seconds": time.monotonic() - started, "immutable_attempt": True})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    command.add_argument("comparison", type=Path)
    command.add_argument("output", type=Path)
    for name in ("verify", "run"):
        commands.add_parser(name).add_argument("experiment", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.comparison.resolve(), args.output.resolve())
    elif args.command == "verify":
        _, plan, _, _ = verify(args.experiment.resolve())
        result = {"verified": True, "recipe": plan["selected_recipe"], "budget": plan["budget"]}
    else:
        result = run(args.experiment.resolve())
    print(json.dumps(result, indent=2))
