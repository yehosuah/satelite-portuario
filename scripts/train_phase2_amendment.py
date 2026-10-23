"""Bounded, preregistered TRAIN-only detector development; never validation.

January fits the inner models and all reviewed February areas select among a
fixed list. No CAL/TEST manifests or label geometries are read by this runner.
The original phase2 runner and experiment receipts remain unchanged.
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import resource
import shutil
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from train_phase2 import inspect_split, runtime_identity, source_hashes, write_json

from puertogt.cli import read_model
from puertogt.history import file_lock
from puertogt.ml.workflows import (
    history_training_diagnostic_inputs,
    history_training_fit_report,
    train_detector,
)
from puertogt.store import confined_path, sha256_file

ROOT = Path(__file__).resolve().parents[1]
PLAN_SHA256 = "871b1cf349e415ad79d1a6eefa4f2a910cb5771b0f84b3bd1d29126d91577c73"


def _train_manifest(root):
    value = json.loads((Path(root) / "manifest.json").read_text())
    if value.get("split") != "train" or not value.get("samples"):
        raise ValueError("Only a nonempty TRAIN manifest is permitted")
    for sample in value["samples"]:
        if sample.get("split", "train") != "train" or sample.get("sensor") != "sentinel-2":
            raise ValueError("Only Sentinel-2 TRAIN samples are permitted")
    return value


def _in_window(sample, window):
    value = datetime.fromisoformat(sample["captured_at"].replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Temporal partition requires timezone-aware acquisition dates")
    low, high = [datetime.fromisoformat(day).replace(tzinfo=timezone.utc) for day in window]
    if low >= high:
        raise ValueError("Temporal partition window is empty")
    return low <= value < high


def freeze_temporal_fold(source, destination, window):
    """Copy every sample in a fixed date window, including negative-only scopes."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("The internal fold must be separate from its original TRAIN source")
    manifest = _train_manifest(source)
    selected = [sample for sample in manifest["samples"] if _in_window(sample, window)]
    if not selected:
        raise ValueError("No TRAIN samples in the predeclared temporal window")
    keys = {sample["acquisition_key"] for sample in selected}
    history = manifest.get("history_inference", {})
    acquisitions = [item for item in history.get("acquisitions", []) if item["acquisition_key"] in keys]
    if {item["acquisition_key"] for item in acquisitions} != keys:
        raise ValueError("Every internal validation acquisition needs its complete original halos")
    files = {}

    def add(relative, digest):
        if relative in files and files[relative] != digest:
            raise ValueError("Conflicting frozen evidence hashes")
        files[relative] = digest

    for sample in selected:
        add(sample["labels"], sample["sha256"])
        for asset in sample.get("evidence_assets", []):
            add(asset["path"], asset["sha256"])
    for acquisition in acquisitions:
        for entry in acquisition["tiles"]:
            for relative, digest in entry["file_sha256"].items():
                add(relative, digest)
    if destination.exists():
        raise FileExistsError("Internal development folds are immutable")
    # Check every source before publishing any destination directory.
    for relative, digest in files.items():
        if sha256_file(confined_path(source, relative)) != digest:
            raise ValueError("Frozen internal development evidence checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".temporal-fold-", dir=destination.parent))
    try:
        for relative, digest in files.items():
            target = confined_path(stage, relative, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(confined_path(source, relative), target)
            if sha256_file(target) != digest:
                raise ValueError("Copied internal development evidence checksum mismatch")
        result = {**manifest, "version": manifest["version"] + "-internal-february-v1",
                  "source_manifest_sha256": sha256_file(source / "manifest.json"),
                  "development_partition": "temporal_validation", "temporal_window": list(window),
                  "samples": selected, "history_inference": {**history, "acquisitions": acquisitions}}
        write_json(stage / "manifest.json", result)
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return result


def assert_disjoint(left, right):
    for field in ("ids", "groups", "numeric_hashes"):
        if set(left[field]) & set(right[field]):
            raise ValueError(f"Internal temporal leakage through {field}")


def select_recipe(rows):
    """Apply the preregistered F1/recall gates; never choose a score threshold."""
    if not rows or len({row["name"] for row in rows}) != len(rows):
        raise ValueError("Unique candidates and the original comparison are required")
    ranked = []
    for index, row in enumerate(rows):
        counts = row["overall"]
        tp, fp, fn = [counts[name] for name in ("true_positives", "false_positives", "false_negatives")]
        if any(type(value) is not int or value < 0 for value in (tp, fp, fn)):
            raise ValueError("Nonnegative integer detection counts are required")
        ranked.append({**row, "declaration_order": index,
                       "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
                       "precision": tp / (tp + fp) if tp + fp else 0,
                       "recall": tp / (tp + fn) if tp + fn else 0})
    original = ranked[0]
    eligible = [row for row in ranked[1:]
                if row["f1"] - original["f1"] >= .05 - 1e-12
                and row["recall"] >= original["recall"]]
    selected = max(eligible, key=lambda row: (row["f1"], row["precision"], row["recall"],
                                             -row["epochs"], -row["learning_rate"],
                                             -row["declaration_order"])) if eligible else None
    return {"candidates": ranked, "selected": selected,
            "selection_scope": "internal TRAIN temporal development only; February previously seen in aggregate fit",
            "fixed_score_threshold": .5, "fixed_iou_threshold": .5,
            "minimum_absolute_f1_gain": .05, "recall_must_not_decrease": True,
            "validation_eligible": False, "automatic_publication": False}


def _inputs(plan):
    descriptors = [plan["inner_training"], plan["inner_validation_source"], plan["full_train"]]
    for descriptor in descriptors:
        root = ROOT / descriptor["path"]
        if sha256_file(root / "manifest.json") != descriptor["sha256"]:
            raise ValueError("Preregistered TRAIN manifest changed")
        _train_manifest(root)
    inner = plan["inner_training"]
    root = ROOT / inner["source_path"]
    if sha256_file(root / "manifest.json") != inner["source_sha256"]:
        raise ValueError("Original January source manifest changed")
    january = _train_manifest(root)
    crops = _train_manifest(ROOT / inner["path"])
    if len(crops["samples"]) != inner["crops"] or any(
            not _in_window(sample, inner["window"]) for sample in [*january["samples"], *crops["samples"]]):
        raise ValueError("Inner fit must contain exactly the predeclared January crops")


def _archive_sources(output):
    paths = list((ROOT / "src/puertogt").rglob("*.py")) + [
        Path(__file__), ROOT / "scripts/train_phase2.py", ROOT / "scripts/diagnose_train_fit.py",
        ROOT / "uv.lock", ROOT / "pyproject.toml", ROOT / "config/models/s2.yaml"]
    result = {}
    for path in paths:
        relative = str(path.relative_to(ROOT))
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        result[relative] = sha256_file(target)
        if result[relative] != sha256_file(path):
            raise ValueError("Source changed while archiving the experiment")
    return result


def freeze(plan_path, output):
    if sha256_file(plan_path) != PLAN_SHA256:
        raise ValueError("Only the explicitly preregistered amendment plan is supported")
    plan = json.loads(plan_path.read_text())
    if sha256_file(ROOT / plan["original_protocol"]["path"]) != plan["original_protocol"]["sha256"]:
        raise ValueError("The original protocol must remain unchanged")
    _inputs(plan)
    base = read_model(ROOT / "config/models/s2.yaml", ROOT / "data")
    if base.weights_sha256 != plan["fixed"]["public_initialization_sha256"]:
        raise ValueError("Every new candidate must initialize from the pinned public model")
    if shutil.disk_usage(ROOT).free < plan["budget"]["minimum_free_disk_bytes"]:
        raise OSError("Insufficient disk reserve")
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "plan.json", plan)
    write_json(output / "base-manifest.json", base.model_dump(mode="json"))
    inner = plan["inner_training"]
    fold = output / "internal-february"
    freeze_temporal_fold(ROOT / plan["inner_validation_source"]["path"], fold,
                         plan["inner_validation_source"]["window"])
    _, left = inspect_split(ROOT / inner["path"], inner["sha256"], "train", base,
                             train_source=ROOT / inner["source_path"])
    _, right = inspect_split(fold, sha256_file(fold / "manifest.json"), "train", base)
    assert_disjoint(left, right)
    inspect_split(ROOT / plan["full_train"]["path"], plan["full_train"]["sha256"], "train", base,
                   train_source=ROOT / plan["inner_validation_source"]["path"])
    legacy = plan["candidates"][0]
    path = ROOT / legacy["reuse_existing_manifest"]
    if sha256_file(path) != legacy["reuse_manifest_sha256"]:
        raise ValueError("Original January trained comparison changed")
    existing = read_model(path, ROOT / "data")
    training = existing.metadata.get("training", {})
    if training.get("dataset_manifest_sha256") != inner["sha256"]:
        raise ValueError("Original comparison must have trained only on the pinned January crops")
    if set(training.get("split_groups", [])) & right["groups"]:
        raise ValueError("Original comparison has seen an internal validation pass")
    write_json(output / "comparison-manifest.json", existing.model_dump(mode="json"))
    receipt = {"schema_version": "phase2-train-only-amendment-freeze-v1", "plan_sha256": PLAN_SHA256,
               "source_sha256": source_hashes(), "runtime": runtime_identity(),
               "archived_source_sha256": _archive_sources(output),
               "fold_sha256": sha256_file(fold / "manifest.json"),
               "base_manifest_sha256": sha256_file(output / "base-manifest.json"),
               "comparison_manifest_sha256": sha256_file(output / "comparison-manifest.json"),
               "inner_partition_groups": {"january": sorted(left["groups"]), "february": sorted(right["groups"])},
               "internal_groups_ids_numeric_hashes_disjoint": True,
               "all_nodata_numeric_hashes_exempt_only": True,
               "held_out_manifests_or_labels_read": False, "automatic_publication": False}
    write_json(output / "freeze.json", receipt)
    return plan, base, receipt


def _assert_frozen(output, receipt):
    if source_hashes() != receipt["source_sha256"] or runtime_identity() != receipt["runtime"]:
        raise ValueError("Amendment source/runtime changed; replay the archived source")
    for relative, digest in receipt["archived_source_sha256"].items():
        if sha256_file(ROOT / relative) != digest or sha256_file(output / "source" / relative) != digest:
            raise ValueError("An archived or active amendment source changed")
    for relative, key in [("internal-february/manifest.json", "fold_sha256"),
                          ("base-manifest.json", "base_manifest_sha256"),
                          ("comparison-manifest.json", "comparison_manifest_sha256")]:
        if sha256_file(output / relative) != receipt[key]:
            raise ValueError("Frozen amendment manifest changed")


@contextmanager
def _heavy_budget(seconds):
    if seconds <= 0:
        raise TimeoutError("Predeclared heavy wall-time budget exhausted")

    def exceeded(signum, frame):
        raise TimeoutError("Predeclared heavy wall-time budget exhausted")

    previous = signal.signal(signal.SIGALRM, exceeded)
    try:
        with file_lock(ROOT / "data/.heavy-worker.lock"):
            signal.setitimer(signal.ITIMER_REAL, seconds)
            yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _seed():
    import numpy as np
    import torch

    random.seed(20261021)
    np.random.seed(20261021)
    torch.manual_seed(20261021)
    torch.set_num_threads(4)


def _measure(dataset, model, destination):
    floor = model.model_copy(update={"publication_policy": {**model.publication_policy, "score_threshold": .1}})
    _, records = history_training_diagnostic_inputs(dataset, floor, cache_dir=destination / "cache")
    reports = [history_training_fit_report(records, threshold=value) for value in [.1, .5, .8]]
    write_json(destination / "predictions.json", records)
    write_json(destination / "report.json", {"reports_at_fixed_thresholds": reports,
                                              "validation_eligible": False, "automatic_publication": False})
    return reports[1]


def run(plan_path, output):
    plan, base, receipt = freeze(plan_path, output)
    elapsed, steps, rows = 0.0, 0, []
    budget = plan["budget"]
    try:
        for candidate in plan["candidates"]:
            _assert_frozen(output, receipt)
            _inputs(plan)
            if shutil.disk_usage(ROOT).free < budget["minimum_free_disk_bytes"]:
                raise OSError("Insufficient disk reserve")
            name = candidate["name"]
            destination = output / "candidates" / name
            start = time.monotonic()
            with _heavy_budget(budget["maximum_total_heavy_wall_seconds"] - elapsed):
                _seed()
                write_json(destination / "started.json", {"candidate": candidate, "plan_sha256": PLAN_SHA256})
                if "reuse_existing_manifest" in candidate:
                    model = read_model(output / "comparison-manifest.json", ROOT / "data")
                else:
                    planned = candidate["epochs"] * plan["inner_training"]["crops"]
                    if steps + planned > budget["maximum_new_inner_optimizer_steps"]:
                        raise ValueError("Predeclared inner training step budget exceeded")
                    train_detector(ROOT / plan["inner_training"]["path"], base, destination / "trained",
                                   epochs=candidate["epochs"], learning_rate=candidate["learning_rate"],
                                   sample_order=candidate["sample_order"], seed=plan["fixed"]["seed"])
                    steps += planned
                    model = read_model(destination / "trained/manifest.json", ROOT / "data")
                report = _measure(output / "internal-february", model, destination / "internal-validation")
                rows.append({**candidate, "weights_sha256": model.weights_sha256,
                             "overall": report["overall"], "ports": report["ports"]})
                write_json(destination / "completed.json", {"candidate": name, "seconds": time.monotonic() - start,
                                                            "weights_sha256": model.weights_sha256,
                                                            "validation_eligible": False})
                del model
                gc.collect()
            elapsed += time.monotonic() - start
            print(json.dumps({"candidate_completed": name, "heavy_seconds": elapsed, "new_inner_steps": steps}), flush=True)
        decision = select_recipe(rows)
        write_json(output / "selection.json", decision)
        selected = decision["selected"]
        final = None
        if selected is not None:
            _assert_frozen(output, receipt)
            _inputs(plan)
            count = len(_train_manifest(ROOT / plan["full_train"]["path"])["samples"])
            if count * selected["epochs"] > budget["maximum_final_optimizer_steps"]:
                raise ValueError("Predeclared final training step budget exceeded")
            if shutil.disk_usage(ROOT).free < budget["minimum_free_disk_bytes"]:
                raise OSError("Insufficient disk reserve")
            start = time.monotonic()
            with _heavy_budget(budget["maximum_total_heavy_wall_seconds"] - elapsed):
                _seed()
                write_json(output / "final.started.json", {"recipe": selected, "plan_sha256": PLAN_SHA256})
                train_detector(ROOT / plan["full_train"]["path"], base, output / "final-trained",
                               epochs=selected["epochs"], learning_rate=selected["learning_rate"],
                               sample_order=selected["sample_order"], seed=plan["fixed"]["seed"])
                model = read_model(output / "final-trained/manifest.json", ROOT / "data")
                report = _measure(ROOT / plan["inner_validation_source"]["path"], model,
                                  output / "final-training-fit")
                final = {"weights_sha256": model.weights_sha256, "manifest_sha256": sha256_file(
                    output / "final-trained/manifest.json"), "training_fit_at_fixed_score_half": report["overall"],
                    "training_steps": count * selected["epochs"], "validation_eligible": False}
                del model
                gc.collect()
            elapsed += time.monotonic() - start
        result = {"status": "completed_internal_development_only", "plan_sha256": PLAN_SHA256,
                  "freeze_sha256": sha256_file(output / "freeze.json"), "new_inner_steps": steps,
                  "heavy_seconds": elapsed, "final_refit": final, "selected_recipe": selected,
                  "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                  * (1 if sys.platform == "darwin" else 1024),
                  "calibration_and_test_used": False, "validation_eligible": False,
                  "automatic_publication": False}
        write_json(output / "completed.json", result)
        print(json.dumps(result, indent=2))
    except BaseException as exc:
        write_json(output / "failed.json", {"error_type": type(exc).__name__, "message": str(exc),
                                             "completed_candidates": rows, "immutable_attempt": True})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
