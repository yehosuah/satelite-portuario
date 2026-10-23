"""Freeze and calibrate a completed TRAIN-only amendment without reopening TEST.

The original per-port support, IoU, precision, recall and threshold grid remain
unchanged. A successful calibration still cannot declare local validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from train_phase2 import (
    PROTOCOL,
    ROOT,
    SELECTION,
    allowed_assignments,
    check_splits,
    finish_stage,
    read_model,
    runtime_identity,
    set_runtime,
    source_hashes,
    start_stage,
    write_json,
)

from puertogt.history import file_lock
from puertogt.ml.workflows import history_evaluation_inputs, select_history_threshold
from puertogt.store import sha256_file


def pinned(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256_file(path)}


def verify_pins(pins: dict) -> None:
    for name, item in pins.items():
        if sha256_file(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"Frozen input changed: {name}")


def source_identity() -> dict:
    return {**source_hashes(), "calibration_runner": sha256_file(Path(__file__))}


def validate_lineage(model, receipt: dict, plan: dict) -> None:
    if (receipt.get("status") != "completed_internal_development_only"
            or receipt.get("calibration_and_test_used") is not False
            or receipt.get("final_refit", {}).get("weights_sha256") != model.weights_sha256
            or receipt.get("plan_sha256") != plan["sha256"]):
        raise ValueError("Candidate must match a completed TRAIN-only amendment")
    if model.task != "detection" or model.publication_policy.get("status") == "validated":
        raise ValueError("An experimental detection candidate is required")


def freeze(args) -> dict:
    pins = {"model": pinned(args.model), "training_receipt": pinned(args.training_receipt),
            "training_plan": pinned(args.training_plan), "protocol": pinned(PROTOCOL),
            "selection": pinned(SELECTION), "execution_order": pinned(args.execution_order)}
    for name in ("model", "training_receipt", "training_plan", "protocol"):
        if pins[name]["sha256"] != getattr(args, name + "_sha256"):
            raise ValueError(f"Explicit {name} checksum mismatch")
    model = read_model(args.model)
    pins["weights"] = pinned(Path(model.weights_path))
    receipt = json.loads(args.training_receipt.read_text())
    validate_lineage(model, receipt, pins["training_plan"])
    datasets = {split: {"path": str(getattr(args, split).resolve()),
                        "sha256": getattr(args, split + "_sha256")}
                for split in ("train", "calibration", "test")}
    plan = json.loads(args.training_plan.read_text())
    trained_data = json.loads((args.train / "manifest.json").read_text())
    if (plan.get("full_train", {}).get("sha256") != datasets["train"]["sha256"]
            or trained_data.get("version") != model.dataset_version):
        raise ValueError("TRAIN must be the dataset used by the candidate refit")
    pins["train_source"] = pinned(args.train_source / "manifest.json")
    checked = check_splits(datasets, model, train_source=args.train_source)
    allowed = allowed_assignments(json.loads(SELECTION.read_text()),
                                  json.loads(args.execution_order.read_text()))
    if any(not item["assignments"] <= allowed for item in checked.values()):
        raise ValueError("Dataset contains an acquisition not selected or activated")
    output = args.output.resolve()
    if any(Path(item["path"]) == output or Path(item["path"]) in output.parents
           for item in datasets.values()):
        raise ValueError("Experiment must be outside frozen datasets")
    output.mkdir(parents=True, exist_ok=False)
    value = {"schema_version": "phase2-amended-candidate-calibration-v1", "pins": pins,
             "datasets": datasets, "source_sha256": source_identity(), "runtime": runtime_identity(),
             "parameters": {"iou": .5, "min_support_per_port": 30, "precision": .9,
                            "recall": .8, "grid": [i / 100 for i in range(10, 100)],
                            "required_ports": ["prq", "stc", "pbr"]},
             "test_label_geometries_opened": False, "test_inference_permitted": False,
             "automatic_publication": False,
             "reference_scope": "Attributed AI visual references on explicit reviewed surfaces; "
                                "some surfaces are detector-conditioned, not whole-port precision."}
    write_json(output / "freeze.json", value)
    return {"frozen": True, "freeze_sha256": sha256_file(output / "freeze.json")}


def load_frozen(experiment: Path) -> tuple[dict, object]:
    value = json.loads((experiment / "freeze.json").read_text())
    verify_pins(value["pins"])
    if source_identity() != value["source_sha256"] or runtime_identity() != value["runtime"]:
        raise ValueError("Frozen inference code or runtime changed")
    model = read_model(Path(value["pins"]["model"]["path"]))
    check_splits(value["datasets"], model,
                 train_source=Path(value["pins"]["train_source"]["path"]).parent)
    return value, model


def run(experiment: Path) -> dict:
    value, model = load_frozen(experiment)
    started = start_stage(experiment, "calibrate")  # exclusive file; never rerun an attempt
    set_runtime()
    floor = model.model_copy(update={"publication_policy": {**model.publication_policy,
                                                           "score_threshold": .10}})
    root = Path(value["datasets"]["calibration"]["path"])
    _, records = history_evaluation_inputs(root, floor, required_split="calibration",
                                          cache_dir=experiment / "calibration/cache")
    report = select_history_threshold(records)
    report.update(model_id=model.model_id, weights_sha256=model.weights_sha256,
                  dataset_sha256=value["datasets"]["calibration"]["sha256"],
                  reference_scope=value["reference_scope"], automatic_publication=False,
                  test_inference_permitted=False, local_validation_demonstrated=False)
    prediction_path = experiment / "calibration/predictions.json"
    report_path = experiment / "calibration/report.json"
    write_json(prediction_path, records)
    write_json(report_path, report)
    return finish_stage(experiment, "calibrate", [prediction_path, report_path], started,
                        passed=bool(report["selected"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    for name in ("model", "training-receipt", "training-plan", "train", "calibration", "test"):
        command.add_argument("--" + name, type=Path, required=True)
        command.add_argument("--" + name + "-sha256", required=True)
    command.add_argument("--protocol-sha256", required=True)
    command.add_argument("--train-source", type=Path, required=True)
    command.add_argument("--execution-order", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    commands.add_parser("run").add_argument("experiment", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args)
    else:
        with file_lock(ROOT / "data/.heavy-worker.lock"):
            result = run(args.experiment.resolve())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
