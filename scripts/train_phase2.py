"""Separate frozen train, calibration and held-out evaluation with original history tiles.

Freeze checks bytes and metadata only. Train and calibration never parse test
label geometries. Started computations are immutable, including failed attempts.
"""

from __future__ import annotations

import argparse
import fcntl
import gc
import hashlib
import importlib
import importlib.metadata
import json
import random
import resource
import sys
import time
from pathlib import Path

from puertogt.contracts import ModelManifest
from puertogt.ml.workflows import (
    history_detection_report,
    history_evaluation_inputs,
    history_pass_id,
    labels_for_sample,
    reviewed_area,
    select_history_threshold,
    validate_history_evidence,
)
from puertogt.store import confined_path, sha256_file

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "reports/local-review/phase2/protocol.md"
SELECTION = ROOT / "reports/local-review/phase2/sampling-plan.json"
EXECUTION_ORDER = ROOT / "reports/local-review/phase2/execution-order.json"
PORTS = ("prq", "stc", "pbr")
SEED, THREADS, EPOCHS, LEARNING_RATE = 20261021, 4, 3, 2e-5
BASES = {
    "s1": ("sentinel-1", .7, "35645431fb58c410603ea3e4af4c7654218389fc8bf953fe3d7f83e5629b5d2a"),
    "s2": ("sentinel-2", .8, "99f9006a8f0d3ca1e5426567713ef03533d9378d1f70ecdb4990b8a98a69e9fe"),
}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def source_hashes() -> dict:
    names = ["history", "preprocess", "review", "models", "ml.workflows", "ml.history_evidence", "ml.detection",
             "ml.training", "ml.evaluation"]
    return {"runner": sha256_file(Path(__file__)), **{
        name: sha256_file(Path(importlib.import_module("puertogt." + name).__file__))
        for name in names}}


def runtime_identity() -> dict:
    return {"python": sys.version, "platform": sys.platform, "lock_sha256": sha256_file(ROOT / "uv.lock"),
            "packages": {name: importlib.metadata.version(name)
                         for name in ("numpy", "rasterio", "shapely", "pyproj", "torch", "torchvision", "rslearn")}}


def raster_has_signal(path: Path) -> bool:
    """Keep leakage checks unless every band is explicitly and entirely nodata.

    Identical empty historical tiles can occur in independent acquisitions.
    A zero without a declared nodata value is data; masks or low variance do
    not justify an exemption. Integrity checks still cover every file byte.
    """
    import numpy as np
    import rasterio

    with rasterio.open(path) as source:
        if any(value is None for value in source.nodatavals):
            return True
        for band, nodata in enumerate(source.nodatavals, 1):
            for _, window in source.block_windows(band):
                values = source.read(band, window=window)
                missing = np.isnan(values) if np.isnan(nodata) else values == nodata
                if not missing.all():
                    return True
    return False


def inspect_split(root: Path, expected_hash: str, split: str, model: ModelManifest,
                  *, train_source: Path | None = None) -> tuple[dict, dict]:
    """Integrity and leakage metadata only; never parse held-out label contents."""
    path = root / "manifest.json"
    if sha256_file(path) != expected_hash:
        raise ValueError(f"Frozen {split} manifest checksum mismatch")
    data = json.loads(path.read_text())
    if data.get("split") != split or not data.get("version") or not data.get("samples"):
        raise ValueError(f"A versioned, nonempty frozen {split} dataset is required")
    ids, groups, numeric_hashes, ports = set(), set(), set(), set()
    all_nodata_hashes = set()

    def record_numeric(path, digest):
        if digest in numeric_hashes or digest in all_nodata_hashes:
            return
        (numeric_hashes if raster_has_signal(path) else all_nodata_hashes).add(digest)

    acquisitions, assignments = set(), set()
    for sample in data["samples"]:
        key = sample["acquisition_key"]
        if sample.get("sensor") != model.sensor or sample.get("split", split) != split:
            raise ValueError("Conflicting frozen sensor or split")
        if sample.get("pass_id") != history_pass_id(key):
            raise ValueError("Frozen pass_id must match the physical acquisition pass")
        if sample["observation_id"] in ids or sample["port_id"] not in PORTS:
            raise ValueError("Duplicate observation or unexpected port")
        if reviewed_area(sample) is None:
            raise ValueError("Every frozen sample needs explicit reviewed_area")
        provenance = sample.get("reference_provenance", {})
        if not provenance.get("author") or provenance.get("kind") != "ai_visual_review":
            raise ValueError("Explicit author and ai_visual_review provenance are required")
        if sha256_file(confined_path(root, sample["labels"])) != sample["sha256"]:
            raise ValueError("Frozen label checksum mismatch")
        for asset in sample.get("evidence_assets", []):
            if sha256_file(confined_path(root, asset["path"])) != asset["sha256"]:
                raise ValueError("Frozen review evidence checksum mismatch")
            if asset.get("metadata", {}).get("bundle", {}).get("bands"):
                record_numeric(confined_path(root, asset["path"]), asset["sha256"])
        ids.add(sample["observation_id"])
        ids.add(sample.get("parent_observation_id", sample["observation_id"]))
        groups.update([key, sample["pass_id"], *sample.get("split_groups", [])])
        acquisitions.add(key)
        assignments.add((split, sample["port_id"], sample["sensor"], key))
        ports.add(sample["port_id"])
    evidence_data, evidence_root = data, root
    if data.get("parent_manifest_sha256"):
        if split != "train" or train_source is None:
            raise ValueError("Derived train requires explicit --train-source with its pinned parent")
        parent_path = train_source / "manifest.json"
        if sha256_file(parent_path) != data["parent_manifest_sha256"]:
            raise ValueError("Training source parent checksum mismatch")
        evidence_data = json.loads(parent_path.read_text())
        if evidence_data.get("split") != "train":
            raise ValueError("Derived train source must itself belong to train")
        evidence_root = train_source
        derivation = data.get("derivation", {})
        if (derivation.get("operation"), derivation.get("size"), derivation.get("overlap")) != (
                "native_grid_crops", 512, 64):
            raise ValueError("Train derivation must preserve native 512 crops with overlap64")
        parent_ids = {sample["observation_id"] for sample in evidence_data["samples"]}
        if any(s.get("parent_observation_id") not in parent_ids for s in data["samples"]):
            raise ValueError("Derived crop has no frozen parent observation")
        for sample in evidence_data["samples"]:
            if sha256_file(confined_path(train_source, sample["labels"])) != sample["sha256"]:
                raise ValueError("Parent training label checksum mismatch")
            for asset in sample.get("evidence_assets", []):
                if sha256_file(confined_path(train_source, asset["path"])) != asset["sha256"]:
                    raise ValueError("Parent training evidence checksum mismatch")
    elif train_source is not None:
        raise ValueError("--train-source is only valid for a derived train dataset")
    validate_history_evidence(evidence_root, evidence_data, model)
    evidence = evidence_data["history_inference"]
    for acquisition in evidence["acquisitions"]:
        key = acquisition["acquisition_key"]
        if acquisition["pass_id"] != history_pass_id(key):
            raise ValueError("History acquisition has an inconsistent pass_id")
        groups.update([key, acquisition["pass_id"]])
        context = acquisition.get("context_acquisition_key")
        if context:
            groups.update([context, history_pass_id(context)])
        for entry in acquisition["tiles"]:
            for path in entry["bundle"]["band_paths"].values():
                record_numeric(confined_path(evidence_root, str(path)), entry["file_sha256"][str(path)])
    if acquisitions != {a["acquisition_key"] for a in evidence["acquisitions"]}:
        raise ValueError("Derived samples must preserve the original acquisition set")
    if split != "train":
        per_pass = {}
        for sample in data["samples"]:
            identity = sample["port_id"], sample["pass_id"]
            if identity in per_pass and per_pass[identity] != sample["acquisition_key"]:
                raise ValueError("Multiple acquisitions per port/pass require segment adjudication")
            per_pass[identity] = sample["acquisition_key"]
    return data, {"ids": ids, "groups": groups, "numeric_hashes": numeric_hashes,
                  "all_nodata_hashes": all_nodata_hashes,
                  "ports": ports, "acquisitions": acquisitions, "assignments": assignments,
                  "history_provenance": {a["acquisition_key"]: a.get("provenance", {}) for a in evidence["acquisitions"]}}


def check_splits(datasets: dict, model: ModelManifest, *, train_source=None) -> dict:
    checked = {}
    for split, descriptor in datasets.items():
        _, checked[split] = inspect_split(Path(descriptor["path"]), descriptor["sha256"], split,
                                         model, train_source=train_source if split == "train" else None)
        if checked[split]["ports"] != set(PORTS):
            raise ValueError(f"{split} must represent all three ports")
    for left, right in [("train", "calibration"), ("train", "test"), ("calibration", "test")]:
        for field in ("ids", "groups", "numeric_hashes"):
            if checked[left][field] & checked[right][field]:
                raise ValueError(f"Split leakage through {field}: {left}/{right}")
    return checked


def read_model(path: Path) -> ModelManifest:
    value = ModelManifest.model_validate_json(path.read_text())
    weights = Path(value.weights_path or "")
    if not weights.is_absolute():
        weights = path.parent / weights
    if not value.weights_sha256 or sha256_file(weights) != value.weights_sha256:
        raise ValueError("Frozen model weights checksum mismatch")
    return value.model_copy(update={"weights_path": str(weights.resolve())})


def load_freeze(experiment: Path) -> tuple[dict, ModelManifest]:
    freeze = json.loads((experiment / "freeze.json").read_text())
    if (sha256_file(PROTOCOL) != freeze["protocol_sha256"]
            or sha256_file(SELECTION) != freeze["selection_sha256"]
            or sha256_file(experiment / "execution-order.json") != freeze["execution_order_sha256"]
            or source_hashes() != freeze["source_sha256"]
            or runtime_identity() != freeze["runtime"]):
        raise ValueError("Frozen protocol, selection or inference/training source changed")
    base = read_model(experiment / "base-manifest.json")
    if sha256_file(experiment / "base-manifest.json") != freeze["base_manifest_sha256"]:
        raise ValueError("Base model manifest changed after freeze")
    check_splits(freeze["datasets"], base,
                 train_source=Path(freeze["train_source"]) if freeze["train_source"] else None)
    return freeze, base


def allowed_assignments(selection: dict, order: dict) -> set:
    """A reserve in the sampling frame is eligible only after a scoped activation."""
    allowed, reserve_slots = set(), set()
    for slot in selection["slots"]:
        prefix = slot["split"], slot["port_id"], slot["sensor"]
        primary = slot.get("selected")
        if primary:
            allowed.add((*prefix, primary["acquisition_key"]))
            reserve_slots.update((*prefix, primary["acquisition_key"], item["acquisition_key"])
                                 for item in slot["reserves"])
    for activation in order.get("activated_reserves", []):
        if (activation.get("used_predictions_or_scores") is not False
                or not activation.get("reason") or not activation.get("port_ids")):
            raise ValueError("Reserve activation requires a documented observability/access cause")
        for port in activation["port_ids"]:
            identity = (activation["split"], port, activation["sensor"],
                        activation["failed_primary_acquisition_key"], activation["reserve_acquisition_key"])
            if identity not in reserve_slots:
                raise ValueError("Reserve activation does not match its sampling slot")
            allowed.add((*identity[:3], identity[-1]))
    return allowed


def require_stage_receipts(experiment: Path, stage: str) -> dict:
    """Reject evaluation before a successful, immutable calibration receipt."""
    if stage not in {"train", "calibrate", "evaluate"}:
        raise ValueError("Unknown phase-2 stage")
    receipts = {}
    for name in (["train", "calibrate"] if stage == "evaluate" else ["train"] if stage == "calibrate" else []):
        path = experiment / f"{name}.completed.json"
        if not path.exists():
            raise ValueError(f"Missing successful {name} receipt")
        receipt = json.loads(path.read_text())
        if receipt.get("freeze_sha256") != sha256_file(experiment / "freeze.json"):
            raise ValueError("Stage receipt belongs to another frozen experiment")
        for relative, digest in receipt.get("outputs", {}).items():
            if sha256_file(confined_path(experiment, relative)) != digest:
                raise ValueError("Completed stage output checksum mismatch")
        if not receipt.get("outputs") or receipt.get("status") != "passed":
            raise ValueError(f"Unsuccessful {name} receipt; do not open the test")
        receipts[name] = receipt
    return receipts


def reference_support(root: Path, dataset: dict) -> dict:
    """Count labels, never predictions; held-out callers must first lock calibration."""
    keys, passes = {p: set() for p in PORTS}, {p: set() for p in PORTS}
    for sample in dataset["samples"]:
        port = sample["port_id"]
        passes[port].add(sample["pass_id"])
        for feature in labels_for_sample(root, sample):
            key = feature.get("properties", {}).get("provenance", {}).get("unique_physical_object_key")
            if not key:
                raise ValueError("Labels must identify unique physical object observations")
            keys[port].add((sample["acquisition_key"], key))
    return {p: {"references": len(keys[p]), "passes": len(passes[p])} for p in PORTS}


def reserve_test_once(freeze: dict, dataset: dict, *, experiment: Path) -> None:
    """Persist consumption by sensor/pass, including attempts that later fail."""
    registry = Path(freeze["test_registry"])
    registry.mkdir(parents=True, exist_ok=True)
    identities = sorted({sample["sensor"] + "|" + sample["pass_id"] for sample in dataset["samples"]})
    files = [registry / (hashlib.sha256(value.encode()).hexdigest() + ".json") for value in identities]
    with (registry / ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if any(path.exists() for path in files):
            raise ValueError("Held-out pass already consumed by a prior test attempt")
        for identity, path in zip(identities, files):
            write_json(path, {"identity": identity, "experiment": str(experiment.resolve()),
                              "freeze_sha256": sha256_file(experiment / "freeze.json"),
                              "test_manifest_sha256": freeze["datasets"]["test"]["sha256"]})


def start_stage(experiment: Path, stage: str) -> float:
    write_json(experiment / f"{stage}.started.json", {"freeze_sha256": sha256_file(experiment / "freeze.json"),
                                                      "stage": stage, "seed": SEED})
    return time.monotonic()


def finish_stage(experiment: Path, stage: str, files: list[Path], started: float, *, passed=True):
    result = {"status": "passed" if passed else "failed", "freeze_sha256": sha256_file(experiment / "freeze.json"),
              "outputs": {str(p.relative_to(experiment)): sha256_file(p) for p in files},
              "seconds": time.monotonic() - started,
              "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)}
    write_json(experiment / f"{stage}.completed.json", result)
    return result


def set_runtime():
    import numpy as np
    import torch
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(THREADS)


def freeze_experiment(args) -> dict:
    from puertogt.cli import read_model as read_base

    if args.protocol_sha256 != sha256_file(PROTOCOL):
        raise ValueError("Explicit approved phase-2 protocol checksum is required")
    base = read_base(ROOT / f"config/models/{args.sensor}.yaml", ROOT / "data")
    sensor, _, digest = BASES[args.sensor]
    if base.sensor != sensor or base.weights_sha256 != digest or sha256_file(Path(base.weights_path)) != digest:
        raise ValueError("Only the pinned public base checkpoint may initialize phase 2")
    datasets = {split: {"path": str(getattr(args, split).resolve()), "sha256": getattr(args, split + "_sha256")}
                for split in ("train", "calibration", "test")}
    checked = check_splits(datasets, base, train_source=args.train_source)
    selection = json.loads(SELECTION.read_text())
    order_bytes = args.execution_order.read_bytes()
    allowed = allowed_assignments(selection, json.loads(order_bytes))
    for split in datasets:
        if not checked[split]["assignments"] <= allowed:
            raise ValueError("Frozen dataset contains an unselected or unactivated acquisition assignment")
    experiment = args.output.resolve()
    if any(Path(item["path"]) == experiment or Path(item["path"]) in experiment.parents
           for item in datasets.values()):
        raise ValueError("Experiment output must be outside frozen datasets")
    experiment.mkdir(parents=True, exist_ok=False)
    (experiment / "execution-order.json").write_bytes(order_bytes)
    write_json(experiment / "base-manifest.json", base.model_dump(mode="json"))
    freeze = {"version": "local-phase2-v1", "sensor": args.sensor, "author": "Codex (AI)",
              "datasets": datasets, "train_source": str(args.train_source.resolve()) if args.train_source else None,
              "protocol_sha256": args.protocol_sha256, "selection_sha256": sha256_file(SELECTION),
              "execution_order_sha256": sha256_file(experiment / "execution-order.json"),
              "source_sha256": source_hashes(), "base_manifest_sha256": sha256_file(experiment / "base-manifest.json"),
              "runtime": runtime_identity(),
              "test_registry": str((ROOT / "data/experiments/phase2-test-consumption").resolve()),
              "parameters": {"seed": SEED, "threads": THREADS, "epochs": EPOCHS, "learning_rate": LEARNING_RATE,
                             "iou": .5, "min_support": 30, "min_test_passes": 10,
                             "calibration_grid": [i / 100 for i in range(10, 100)], "precision": .9, "recall": .8},
              "history_provenance": {s: d["history_provenance"] for s, d in checked.items()},
              "all_nodata_hash_exemptions": {s: sorted(d["all_nodata_hashes"]) for s, d in checked.items()},
              "test_label_geometries_opened": False, "automatic_publication": False}
    write_json(experiment / "freeze.json", freeze)
    return {"frozen": True, "freeze_sha256": sha256_file(experiment / "freeze.json"), "trained": False}


def run_train(experiment: Path) -> dict:
    from puertogt.ml.workflows import train_detector

    freeze, base = load_freeze(experiment)
    require_stage_receipts(experiment, "train")
    root = Path(freeze["datasets"]["train"]["path"])
    data = json.loads((root / "manifest.json").read_text())
    support = reference_support(root, data)
    if any(support[p]["references"] == 0 for p in PORTS):
        raise ValueError("Real positive train labels are required in each port")
    started = start_stage(experiment, "train")
    set_runtime()
    identity = sha256_file(experiment / "freeze.json")[:12]
    base = base.model_copy(update={"model_id": base.model_id + "-phase2-" + identity,
                                   "version": base.version + "+phase2-" + identity})
    train_detector(root, base, experiment / "trained", epochs=EPOCHS, learning_rate=LEARNING_RATE,
                   device="cpu", reserved_manifests=[Path(freeze["datasets"][s]["path"]) / "manifest.json"
                                                     for s in ("calibration", "test")])
    write_json(experiment / "train-support.json", support)
    return finish_stage(experiment, "train", [experiment / "trained/manifest.json",
                                              experiment / "trained/weights.pt", experiment / "train-support.json"], started)


def run_calibration(experiment: Path) -> dict:
    freeze, _ = load_freeze(experiment)
    require_stage_receipts(experiment, "calibrate")
    model = read_model(experiment / "trained/manifest.json")
    floor = model.model_copy(update={"publication_policy": {**model.publication_policy, "score_threshold": .10}})
    started = start_stage(experiment, "calibrate")
    set_runtime()
    _, records = history_evaluation_inputs(Path(freeze["datasets"]["calibration"]["path"]), floor,
                                           required_split="calibration", cache_dir=experiment / "calibration/cache")
    report = select_history_threshold(records)
    report.update(freeze_sha256=sha256_file(experiment / "freeze.json"), weights_sha256=model.weights_sha256)
    write_json(experiment / "calibration/predictions.json", records)
    write_json(experiment / "calibration/report.json", report)
    outputs = [experiment / "calibration/report.json", experiment / "calibration/predictions.json"]
    if report["selected"]:
        calibrated = model.model_copy(update={
            "weights_path": "../trained/weights.pt",
            "publication_policy": {**model.publication_policy, "score_threshold": report["selected"]["threshold"],
                                   "automatic_publication": False, "local_validation": "calibration_only"},
            "metadata": {**model.metadata, "phase2_freeze_sha256": sha256_file(experiment / "freeze.json")}})
        write_json(experiment / "calibration/manifest.json", calibrated.model_dump(mode="json"))
        outputs.append(experiment / "calibration/manifest.json")
    return finish_stage(experiment, "calibrate", outputs, started, passed=bool(report["selected"]))


def run_evaluation(experiment: Path) -> dict:
    require_stage_receipts(experiment, "evaluate")
    freeze, base = load_freeze(experiment)
    root = Path(freeze["datasets"]["test"]["path"])
    data = json.loads((root / "manifest.json").read_text())
    support = reference_support(root, data)
    if any(support[p]["references"] < 30 or support[p]["passes"] < 10 for p in PORTS):
        raise ValueError("Test requires 30 unique references and ten passes in every port before inference")
    model = read_model(experiment / "calibration/manifest.json")
    started = start_stage(experiment, "evaluate")
    reserve_test_once(freeze, data, experiment=experiment)
    set_runtime()
    base = base.model_copy(update={"publication_policy": {**base.publication_policy,
                                                          "score_threshold": BASES[freeze["sensor"]][1]}})
    outputs, comparisons = [], {}
    for name, candidate in [("base", base), ("adjusted", model)]:
        _, records = history_evaluation_inputs(root, candidate, required_split="test",
                                               cache_dir=experiment / "test" / name / "cache")
        report = history_detection_report(records, threshold=candidate.publication_policy["score_threshold"], split="test")
        report.update(model_id=candidate.model_id, weights_sha256=candidate.weights_sha256,
                      dataset_sha256=freeze["datasets"]["test"]["sha256"],
                      freeze_sha256=sha256_file(experiment / "freeze.json"))
        path = experiment / "test" / name
        write_json(path / "predictions.json", records)
        write_json(path / "report.json", report)
        outputs.extend([path / "predictions.json", path / "report.json"])
        comparisons[name] = report
        del records
        gc.collect()
    write_json(experiment / "test/comparison.json", {
        "models": comparisons, "support_before_inference": support, "model_selection_performed": False,
        "automatic_publication": False, "scope": "Precision against frozen AI references; no threshold changes on test"})
    outputs.append(experiment / "test/comparison.json")
    return finish_stage(experiment, "evaluate", outputs, started)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze", help="Verify integrity and reserve splits without opening test geometries")
    freeze.add_argument("--sensor", choices=BASES, required=True)
    for split in ("train", "calibration", "test"):
        freeze.add_argument("--" + split, type=Path, required=True)
        freeze.add_argument("--" + split + "-sha256", required=True)
    freeze.add_argument("--train-source", type=Path)
    freeze.add_argument("--protocol-sha256", required=True)
    freeze.add_argument("--execution-order", type=Path, default=EXECUTION_ORDER,
                        help="Immutable order declaring activated reserves; defaults to original v1")
    freeze.add_argument("--output", type=Path, required=True)
    for stage in ("train", "calibrate", "evaluate"):
        commands.add_parser(stage).add_argument("experiment", type=Path)
    return result


def main():
    args = parser().parse_args()
    if args.command == "freeze":
        result = freeze_experiment(args)
    else:
        from puertogt.history import file_lock
        with file_lock(ROOT / "data/.heavy-worker.lock"):
            result = {"train": run_train, "calibrate": run_calibration, "evaluate": run_evaluation}[
                args.command](args.experiment.resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
