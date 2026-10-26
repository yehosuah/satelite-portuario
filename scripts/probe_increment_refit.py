"""Freeze and probe the selected S2 refit on new TRAIN passes, without training."""

from __future__ import annotations

import argparse
import importlib
import json
import resource
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from train_phase2 import runtime_identity, write_json

from puertogt.cli import read_model
from puertogt.history import file_lock
from puertogt.ml.detection import detector_anchor_sizes
from puertogt.ml.workflows import (
    history_pass_id,
    history_training_diagnostic_inputs,
    history_training_fit_report,
    labels_for_sample,
    validate_history_evidence,
)
from puertogt.store import confined_path, sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[1]
DATASET = "data/datasets/scientific-closure-20261026/s2-increment-v1/source"
MODEL = "data/experiments/train-anchor-refit-v1-20261026/trained/manifest.json"
FIT = "data/datasets/phase2-reviews/s2-january-february-v1/train-crops/manifest.json"
MODEL_SHA256 = "452db61c4cbd9d564cb1ee36c6366fa8fac95e4508a6b8a921bb20298816f3ba"
WEIGHTS_SHA256 = "e0990d7d69750650a5f5175361fc901de882ad0be35ef8fcfd62a3c4b8e73c68"
FIT_SHA256 = "c5a9b7e542d6a61b09ab5b926aa9cb9f10ee5cb1ac0de172144155b2a5099adc"
SETTINGS = {"score_threshold": .5, "iou_threshold": .5, "cpu_threads": 4,
            "maximum_seconds": 180, "maximum_peak_rss_bytes": 4 * 1024**3,
            "memory_enforcement": "peak RSS checked between stages; no portable OS hard cap"}
INTERPRETATION = (
    "Internal TRAIN development on passes absent from this refit's training. "
    "AI-adjudicated references were exposed to the old public model; this is not independent "
    "validation, calibration, threshold selection, training or model activation."
)


def train_identity(dataset):
    """Read only grouping metadata before any label or numeric file is opened."""
    if dataset.get("split") != "train" or not dataset.get("samples"):
        raise ValueError("A nonempty TRAIN dataset is required")
    samples = dataset["samples"]
    if any(sample.get("split", "train") != "train" or sample.get("sensor") != "sentinel-2"
           for sample in samples):
        raise ValueError("Every sample must belong to Sentinel-2 TRAIN")
    if any(sample.get("pass_id") != history_pass_id(sample["acquisition_key"])
           for sample in samples):
        raise ValueError("Every TRAIN pass_id must match its canonical acquisition_key")
    ids = {sample["observation_id"] for sample in samples}
    if len(ids) != len(samples):
        raise ValueError("Duplicate TRAIN observation identity")
    return {
        "ids": ids | {sample.get("parent_observation_id", sample["observation_id"])
                      for sample in samples},
        "passes": {history_pass_id(sample["acquisition_key"]) for sample in samples},
        "groups": {group for sample in samples for group in
                   [sample["pass_id"], sample["acquisition_key"], *sample.get("split_groups", [])]},
    }


def validate_unseen_passes(dataset, original_fit, model):
    current, prior = train_identity(dataset), train_identity(original_fit)
    training = model.metadata.get("training", {})
    prior["ids"].update(training.get("observation_ids", []))
    prior["groups"].update(training.get("split_groups", []))
    for field in ("ids", "passes", "groups"):
        if current[field] & prior[field]:
            raise ValueError(f"New TRAIN and refit training overlap through {field}")
    return {"new_pass_ids": sorted(current["passes"]), "fit_pass_ids": sorted(prior["passes"])}


def dataset_pins(root, dataset):
    """Pin labels, review evidence and every original inference tile including halos."""
    train_identity(dataset)
    pins = {"manifest.json": sha256_file(root / "manifest.json")}

    def add(relative, digest):
        if relative in pins and pins[relative] != digest:
            raise ValueError("Conflicting frozen checksum declarations")
        pins[relative] = digest

    for sample in dataset["samples"]:
        add(sample["labels"], sample["sha256"])
        for asset in sample.get("evidence_assets", []):
            add(asset["path"], asset["sha256"])
    for acquisition in dataset["history_inference"]["acquisitions"]:
        for entry in acquisition["tiles"]:
            for relative, digest in entry["file_sha256"].items():
                add(relative, digest)
    for relative, digest in pins.items():
        if sha256_file(confined_path(root, relative)) != digest:
            raise ValueError(f"Frozen TRAIN checksum mismatch: {relative}")
    return pins


def source_state():
    for name in ("models", "history", "ml.workflows", "ml.detection"):
        module = importlib.import_module("puertogt." + name)
        if Path(module.__file__).resolve() != ROOT / "src/puertogt" / (name.replace(".", "/") + ".py"):
            raise ValueError("Run the working source with PYTHONPATH=src; installed copies are forbidden")
    paths = [*(ROOT / "src/puertogt").rglob("*.py"), Path(__file__),
             ROOT / "scripts/train_phase2.py", ROOT / "pyproject.toml", ROOT / "uv.lock"]
    return {"files": {str(path.relative_to(ROOT)): sha256_file(path) for path in paths},
            "runtime": runtime_identity()}


def inspect_inputs(dataset_sha256):
    manifest = confined_path(ROOT, DATASET + "/manifest.json")
    if sha256_file(manifest) != dataset_sha256:
        raise ValueError("Explicit TRAIN manifest checksum mismatch")
    dataset = json.loads(manifest.read_text())
    train_identity(dataset)  # Reject every reserved/mixed sample before reading any label.
    for relative, digest in ((MODEL, MODEL_SHA256), (FIT, FIT_SHA256)):
        if sha256_file(confined_path(ROOT, relative)) != digest:
            raise ValueError("Pinned refit model or original TRAIN manifest changed")
    model = read_model(ROOT / MODEL, ROOT / "data")
    original_fit = json.loads((ROOT / FIT).read_text())
    training = model.metadata.get("training", {})
    if (model.sensor != "sentinel-2" or model.task != "detection"
            or model.weights_sha256 != WEIGHTS_SHA256
            or training.get("dataset_manifest_sha256") != FIT_SHA256
            or detector_anchor_sizes(model) != [[8], [16], [32], [64]]
            or sha256_file(Path(model.weights_path)) != WEIGHTS_SHA256):
        raise ValueError("Only the selected, unchanged e099 TRAIN refit is permitted")
    identity = validate_unseen_passes(dataset, original_fit, model)
    pins = dataset_pins(manifest.parent, dataset)
    validate_history_evidence(manifest.parent, dataset, model)
    objects = {port: set() for port in ("prq", "pbr", "stc")}
    for sample in dataset["samples"]:
        for feature in labels_for_sample(manifest.parent, sample):
            key = feature.get("properties", {}).get("provenance", {}).get("unique_physical_object_key")
            if not key:
                raise ValueError("Every reference needs a frozen physical object identity")
            objects[sample["port_id"]].add((sample["acquisition_key"], key))
    support = {port: len(values) for port, values in objects.items()}
    if (len(dataset["samples"]) != 6 or len(identity["new_pass_ids"]) != 4
            or support != {"prq": 16, "pbr": 0, "stc": 1}):
        raise ValueError("Expected exactly six S2 TRAIN scopes, four new passes and PRQ16/PBR0/STC1")
    return model, {"dataset": DATASET, "dataset_sha256": dataset_sha256, "dataset_files": pins,
                   "model_manifest": MODEL, "model_manifest_sha256": MODEL_SHA256,
                   "weights_sha256": WEIGHTS_SHA256, "original_fit_manifest": FIT,
                   "original_fit_sha256": FIT_SHA256, "scopes": 6, "references_by_port": support,
                   **identity}


def separate_output(output):
    output = Path(output).resolve()
    for root in (ROOT / DATASET, (ROOT / MODEL).parent, (ROOT / FIT).parent):
        if output == root or output in root.parents or root in output.parents:
            raise ValueError("Probe output must be separate from frozen inputs")
    return output


def freeze(dataset_sha256, output):
    output = separate_output(output)
    if output.exists():
        raise FileExistsError("Probe freezes and attempts are immutable")
    _, inputs = inspect_inputs(dataset_sha256)
    plan = {"schema_version": "increment-refit-train-probe-v1", "created_at": utc_text(),
            "inputs": inputs, "settings": SETTINGS, "sources": source_state(),
            "interpretation": INTERPRETATION, "independent_validation": False,
            "calibration_or_test_permitted": False, "automatic_publication": False}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    write_json(output / "freeze.json", {"plan_sha256": sha256_file(output / "plan.json"),
                                       "created_at": utc_text()})
    return {"frozen": True, "freeze_sha256": sha256_file(output / "freeze.json"),
            "plan_sha256": sha256_file(output / "plan.json"), "inputs": inputs,
            "settings": SETTINGS}


def verify_frozen(output, expected_freeze_sha256):
    output = separate_output(output)
    if sha256_file(output / "freeze.json") != expected_freeze_sha256:
        raise ValueError("Explicit freeze checksum mismatch")
    receipt = json.loads((output / "freeze.json").read_text())
    if sha256_file(output / "plan.json") != receipt["plan_sha256"]:
        raise ValueError("Frozen plan checksum mismatch")
    plan = json.loads((output / "plan.json").read_text())
    if (plan["schema_version"] != "increment-refit-train-probe-v1"
            or plan["settings"] != SETTINGS or plan["sources"] != source_state()
            or plan["independent_validation"] is not False
            or plan["calibration_or_test_permitted"] is not False
            or plan["automatic_publication"] is not False):
        raise ValueError("Frozen settings, sources, runtime or TRAIN-only purpose changed")
    model, inputs = inspect_inputs(plan["inputs"]["dataset_sha256"])
    if inputs != plan["inputs"]:
        raise ValueError("Frozen input pins or pass identities changed")
    return plan, model


def peak_rss_bytes():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)


def check_memory():
    if peak_rss_bytes() > SETTINGS["maximum_peak_rss_bytes"]:
        raise MemoryError("Probe exceeded the four GiB peak RSS guard")


@contextmanager
def probe_budget():
    def expired(signum, frame):
        raise TimeoutError("Probe exceeded its global 180-second budget")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, SETTINGS["maximum_seconds"])
    try:
        with file_lock(ROOT / "data/.heavy-worker.lock"):
            yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def run(output, expected_freeze_sha256):
    output = separate_output(output)
    if any((output / name).exists() for name in ("started.json", "completed.json", "failed.json")):
        raise FileExistsError("A frozen probe attempt cannot be repeated")
    # The caller pins the reviewed freeze; exclusive creation fences simultaneous starts.
    if sha256_file(output / "freeze.json") != expected_freeze_sha256:
        raise ValueError("Explicit freeze checksum mismatch")
    write_json(output / "started.json", {"created_at": utc_text(),
                                         "freeze_sha256": expected_freeze_sha256})
    started = time.monotonic()
    try:
        with probe_budget():
            plan, model = verify_frozen(output, expected_freeze_sha256)
            import torch

            torch.set_num_threads(4)
            check_memory()
            probe = model.model_copy(update={"publication_policy": {
                **model.publication_policy, "score_threshold": .5, "automatic_publication": False}})
            _, records = history_training_diagnostic_inputs(
                ROOT / DATASET, probe, cache_dir=output / "cache", device="cpu")
            check_memory()
            report = history_training_fit_report(records, threshold=.5)
            report.update(development_purpose="unseen_refit_passes_internal_train_development",
                          interpretation=INTERPRETATION, independent_validation=False,
                          unseen_by_refit_training=True, new_pass_ids=plan["inputs"]["new_pass_ids"])
            write_json(output / "predictions.json", records)
            write_json(output / "report.json", report)
            result = {"status": "completed_internal_train_development_only",
                      "freeze_sha256": expected_freeze_sha256, "weights_sha256": WEIGHTS_SHA256,
                      "seconds": time.monotonic() - started, "peak_rss_bytes": peak_rss_bytes(),
                      "settings": SETTINGS, "report_sha256": sha256_file(output / "report.json"),
                      "predictions_sha256": sha256_file(output / "predictions.json"),
                      "overall": report["overall"], "ports": report["ports"],
                      "interpretation": INTERPRETATION, "unseen_by_refit_training": True,
                      "calibration_or_test_opened": False, "training_performed": False,
                      "threshold_selection_performed": False, "automatic_publication": False,
                      "independent_validation": False, "model_activation_performed": False}
            check_memory()
            write_json(output / "completed.json", result)
        return result
    except BaseException as exc:
        write_json(output / "failed.json", {"error_type": type(exc).__name__, "message": str(exc),
            "seconds": time.monotonic() - started, "peak_rss_bytes": peak_rss_bytes(),
            "immutable_attempt": True, "automatic_publication": False})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    command.add_argument("--dataset-sha256", required=True)
    command.add_argument("--output", type=Path, required=True)
    for name in ("verify", "run"):
        command = commands.add_parser(name)
        command.add_argument("experiment", type=Path)
        command.add_argument("--freeze-sha256", required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.dataset_sha256, args.output)
    elif args.command == "verify":
        plan, _ = verify_frozen(args.experiment, args.freeze_sha256)
        result = {"verified": True, "inputs": plan["inputs"], "settings": plan["settings"]}
    else:
        result = run(args.experiment, args.freeze_sha256)
    print(json.dumps(result, indent=2))
