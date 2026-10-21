"""Two fixed TRAIN forwards; run only after the coordinator releases history."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "minimal-train-inference-runner-manifest.json"
PLAN_SHA256 = "42e1a38d925ab1def630164bf5f9dc2edc6bcf94768cd4aca4cdd375c7fb04a7"
SAMPLE_ID = "556e10c1-71d5-4003-afc1-a1b1aeef81c8:crop:0:0:512:448"
SCORE_FLOOR = 0.05


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def checked(entry: dict, root: Path = ROOT) -> Path:
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Expected a confined relative input path")
    path = (root / relative).resolve(strict=True)
    path.relative_to(root.resolve())
    if digest(path) != entry["sha256"]:
        raise ValueError(f"Pinned input changed: {relative}")
    return path


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: dict) -> None:
    # Only this newly created diagnostic directory is writable. A completed
    # result appears atomically; every existing output remains untouched.
    partial = path.with_name(path.name + ".partial")
    if path.exists():
        raise FileExistsError(path)
    with partial.open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def box_relation(prediction, reference) -> dict:
    ax, ay, bx, by = map(float, prediction)
    cx, cy, dx, dy = map(float, reference)
    intersection = max(0.0, min(bx, dx) - max(ax, cx)) * max(0.0, min(by, dy) - max(ay, cy))
    union = (bx - ax) * (by - ay) + (dx - cx) * (dy - cy) - intersection
    return {"center_distance_pixels": math.hypot((ax + bx - cx - dx) / 2, (ay + by - cy - dy) / 2),
            "iou": intersection / union if union > 0 else 0.0,
            "width_pixels": bx - ax, "height_pixels": by - ay}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory under tmp/train-diagnostics/")
    parser.add_argument("--coordinator-released-history", action="store_true",
                        help="Required explicit acknowledgement of the agreed compute boundary")
    args = parser.parse_args()
    if not args.coordinator_released_history:
        parser.error("Wait for the coordinator's history boundary; no inference was started")
    if Path.cwd().resolve() != ROOT:
        parser.error(f"Run from the repository root: {ROOT}")
    output = args.output.resolve()
    output.relative_to(ROOT / "tmp/train-diagnostics")
    if output == ROOT / "tmp/train-diagnostics" or output.exists():
        raise FileExistsError("Choose a new named diagnostic output directory")

    runner = json.loads(MANIFEST.read_text())
    checked(runner["runner"])
    plan_path = checked(runner["plan"])
    if digest(plan_path) != PLAN_SHA256:
        raise ValueError("This runner is restricted to the previously registered TRAIN plan")
    plan = json.loads(plan_path.read_text())
    if plan["sample"]["observation_id"] != SAMPLE_ID:
        raise ValueError("The preselected TRAIN crop changed")
    for source in runner["source_files"]:
        checked(source)
    for package, expected in runner["packages"].items():
        if importlib.metadata.version(package) != expected:
            raise ValueError(f"Pinned package version changed: {package}")
    train_path = checked(plan["train_manifest"])
    dataset = json.loads(train_path.read_text())
    if dataset.get("split") != "train":
        raise ValueError("Only the fixed TRAIN split is authorized")
    selected = [item for item in dataset["samples"] if item["observation_id"] == SAMPLE_ID]
    if len(selected) != 1:
        raise ValueError("The selected crop must occur exactly once")
    sample, train_dir = selected[0], train_path.parent
    for key in ("acquisition_key", "crop_window", "labels", "sha256", "reviewed_pixel_count"):
        if sample[key] != plan["sample"][key]:
            raise ValueError(f"Selected TRAIN sample changed: {key}")
    checked({"path": sample["labels"], "sha256": sample["sha256"]}, train_dir)
    for asset in sample["evidence_assets"]:
        checked(asset, train_dir)
    for entry in plan["model_manifests"]:
        checked(entry)

    # This is the identical nonblocking OS lock used by history and phase2.
    # Busy means an immediate failure before loading torch or either checkpoint.
    from puertogt.history import file_lock
    with file_lock(ROOT / "data/.heavy-worker.lock"):
        import numpy as np
        import torch
        from puertogt.cli import read_model
        from puertogt.ml.detection import load_detector
        from puertogt.ml.workflows import labels_for_sample, pixel_boxes, reviewed_training_mask
        from puertogt.preprocess import read_model_input
        from puertogt.review import bundle_from_evidence

        torch.set_num_threads(4)
        torch.set_num_interop_threads(1)
        torch.manual_seed(20261021)
        output.mkdir(parents=True, exist_ok=False)
        started = {"started_at": now(), "schema_version": "fixed-train-diagnostic-start-v1",
                   "runner_manifest": {"path": str(MANIFEST.relative_to(ROOT)), "sha256": digest(MANIFEST)},
                   "plan_sha256": PLAN_SHA256, "sample_id": SAMPLE_ID, "threads": 4,
                   "head_score_floor": SCORE_FLOOR, "forwards_planned": 2,
                   "history_lock_acquired": True, "calibration_or_test_access": False}
        write_json(output / "started.json", started)
        completed = []
        try:
            bundle = bundle_from_evidence(sample, train_dir)
            image = None
            for entry in plan["model_manifests"]:
                model = read_model(ROOT / entry["path"], ROOT / "data")
                if model.sensor != "sentinel-2" or model.recipe_version != "ai2-s2-v1":
                    raise ValueError("Unexpected model contract")
                prepared = read_model_input(bundle, model)
                if prepared.shape != (9, 448, 512) or prepared.dtype != np.float32:
                    raise ValueError("Expected the fixed nine-band 448x512 TRAIN crop")
                if image is None:
                    image = prepared
                elif not np.array_equal(prepared, image):
                    raise ValueError("Base and trained models must receive identical numeric input")
                padded = np.pad(image, ((0, 0), (0, (-image.shape[1]) % 32), (0, (-image.shape[2]) % 32)))
                if not np.isfinite(padded).all():
                    raise ValueError("Non-finite numeric input")
                targets = pixel_boxes(labels_for_sample(train_dir, sample), bundle, 448, 512)
                if targets.shape != (1, 4):
                    raise ValueError("The registered crop has exactly one visual reference")
                scope = reviewed_training_mask(image, bundle, sample)
                if int(scope.sum()) != plan["sample"]["reviewed_pixel_count"]:
                    raise ValueError("Frozen review mask changed")
                load_start = time.monotonic()
                detector = load_detector(model, device="cpu")
                head = detector.model.decoders["detect"][-1]
                if not math.isclose(head.roi_heads.score_thresh, SCORE_FLOOR, abs_tol=1e-12):
                    raise ValueError("Existing head score floor changed; never retune it here")
                load_seconds = time.monotonic() - load_start
                forward_start = time.monotonic()
                raw = detector.raw_predict(padded)  # Exactly one forward per registered model.
                forward_seconds = time.monotonic() - forward_start
                for value in raw.values():
                    if not np.isfinite(value).all():
                        raise ValueError("Non-finite model output")
                if len(raw["boxes"]) > 100 or len(raw["boxes"]) != len(raw["scores"]):
                    raise ValueError("Unexpected output contract")
                reference = targets[0]
                relations = []
                for index, (box, score, label) in enumerate(zip(raw["boxes"], raw["scores"], raw["labels"])):
                    if float(score) < SCORE_FLOOR - 1e-7:
                        raise ValueError("Output below the pinned head floor")
                    cx, cy = (float(box[0] + box[2]) / 2, float(box[1] + box[3]) / 2)
                    inside = 0 <= cx < 512 and 0 <= cy < 448
                    relations.append({"index": index, "label": int(label), "score": float(score),
                                      "box": box.tolist(), **box_relation(box, reference),
                                      "center_inside_crop": inside,
                                      "center_inside_reviewed_scope": bool(inside and scope[int(cy), int(cx)])})
                nearest = min(relations, key=lambda row: (row["center_distance_pixels"], row["index"]), default=None)
                result = {"schema_version": "fixed-train-crop-raw-predictions-v1", "role": entry["role"],
                          "model_manifest": entry, "model_id": model.model_id,
                          "weights_sha256": model.weights_sha256, "sample_id": SAMPLE_ID,
                          "input_array_shape": list(padded.shape),
                          "input_array_sha256": hashlib.sha256(padded.tobytes()).hexdigest(),
                          "raw_outputs": {key: value.tolist() for key, value in raw.items()},
                          "reference_visual_box_unchanged": reference.tolist(),
                          "comparisons": relations, "nearest_center_prediction": nearest,
                          "head_score_floor": SCORE_FLOOR, "secondary_thresholds": [],
                          "load_seconds": load_seconds, "forward_seconds": forward_seconds,
                          "scope": "TRAIN geometry diagnostic only; no generalization metric or validation",
                          "unreviewed_candidates_are_not_false_positives": True,
                          "official_iou_criterion_unchanged": 0.5}
                result_path = output / f"{entry['role']}.json"
                write_json(result_path, result)
                completed.append({"role": entry["role"], "path": result_path.name, "sha256": digest(result_path)})
                del detector, raw, prepared, padded, head
                gc.collect()
            if len(completed) != 2:
                raise ValueError("Expected exactly the two registered forwards")
            write_json(output / "receipt.json", {**started, "completed_at": now(), "results": completed,
                       "forwards_completed": len(completed), "database_writes": False,
                       "weights_or_protocol_changes": False, "optimizer_created": False,
                       "validation_status": "not_assessed_train_diagnostic_only"})
        except BaseException as exc:
            write_json(output / "failure.json", {"failed_at": now(), "type": type(exc).__name__,
                       "message": str(exc), "completed_results": completed,
                       "do_not_overwrite_or_resume_this_output": True})
            raise
    print(json.dumps({"output": str(output), "forwards": 2, "validation": "not_assessed"}))


if __name__ == "__main__":
    main()
