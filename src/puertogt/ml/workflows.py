"""Reproducible supervised workflows exposed by the local CLI.

Training consumes reviewed, frozen labels. Evaluation re-runs inference on a
separate split and never turns a detector score into an accuracy measurement.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import Affine
from rasterio.features import geometry_mask
from shapely.geometry import shape
from shapely.ops import transform as project

from puertogt.contracts import ModelManifest
from puertogt.review import bundle_from_evidence
from puertogt.store import confined_path, json_text, sha256_file


def reviewed_area(sample: dict):
    """Use frozen WGS84 review coverage; legacy public samples cover their full crop."""
    value = sample.get("reviewed_area")
    if value is None:
        return None
    area = shape(value)
    if area.geom_type not in {"Polygon", "MultiPolygon"} or area.is_empty or not area.is_valid:
        raise ValueError("Invalid frozen reviewed area")
    west, south, east, north = area.bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Reviewed area must use WGS84 longitude/latitude")
    return area


def review_scope(sample: dict) -> str:
    return "frozen_area" if sample.get("reviewed_area") is not None else "full_crop"


def mask_reviewed_image(image: np.ndarray, bundle, sample: dict) -> np.ndarray:
    area = reviewed_area(sample)
    if area is None:
        return image
    transformer = Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True)
    inside = geometry_mask(
        [project(transformer.transform, area)],
        out_shape=image.shape[-2:],
        transform=Affine(*bundle.transform),
        invert=True,
    )
    if not inside.any():
        raise ValueError("Frozen reviewed area does not overlap the numeric crop")
    return np.where(inside[None], image, 0)


def labels_for_sample(root: Path, sample: dict) -> list[dict]:
    path = confined_path(root, sample["labels"])
    if sha256_file(path) != sample["sha256"]:
        raise ValueError("Reviewed label checksum mismatch")
    features = json.loads(path.read_text())["features"]
    area = reviewed_area(sample)
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
            raise ValueError("Detector reference labels must be nonempty area geometries")
        if area is not None and not area.covers(geometry.centroid):
            raise ValueError("Reference label lies outside the frozen reviewed area")
    return features


def pixel_boxes(features: list[dict], bundle, height: int, width: int) -> np.ndarray:
    to_grid = Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True)
    inverse = ~Affine(*bundle.transform)
    boxes = []
    for feature in features:
        geometry = project(to_grid.transform, shape(feature["geometry"]))
        corners = [inverse * point for point in geometry.envelope.exterior.coords]
        x, y = zip(*corners)
        box = [max(0, min(x)), max(0, min(y)), min(width, max(x)), min(height, max(y))]
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Reviewed label lies outside its numeric crop")
        boxes.append(box)
    return np.asarray(boxes, dtype=np.float32).reshape(-1, 4)


def train_detector(
    dataset_dir: Path,
    base: ModelManifest,
    destination: Path,
    *,
    epochs: int = 1,
    learning_rate: float = 2e-5,
    device: str = "cpu",
    reserved_manifests=(),
) -> ModelManifest:
    from puertogt.ml.detection import load_detector
    from puertogt.ml.training import export_training_artifact, validate_training_dataset
    from puertogt.preprocess import read_model_input

    dataset = validate_training_dataset(dataset_dir, reserved_manifests=reserved_manifests)
    if not 1 <= epochs <= 100 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("Use 1..100 epochs and a positive finite learning rate")
    detector = load_detector(base, device=device)
    optimizer = detector.make_optimizer(learning_rate=learning_rate)
    steps, started = [], time.monotonic()
    for _ in range(epochs):
        for sample in dataset["samples"]:
            bundle = bundle_from_evidence(sample, dataset_dir)
            image = read_model_input(bundle, base)
            height, width = image.shape[-2:]
            if max(height, width) > 512:
                raise ValueError("Training scenes must be reviewed crops of at most 512 pixels")
            boxes = pixel_boxes(labels_for_sample(dataset_dir, sample), bundle, height, width)
            image = mask_reviewed_image(image, bundle, sample)
            padded = np.pad(image, ((0, 0), (0, (-height) % 32), (0, (-width) % 32)))
            steps.append(detector.frozen_train_step(padded, boxes, optimizer=optimizer))
    return export_training_artifact(
        detector.model,
        base,
        destination,
        dataset_version=dataset["version"],
        training={
            "dataset_manifest_sha256": dataset["manifest_sha256"],
            "reserved_manifest_hashes": dataset["reserved_manifest_hashes"],
            "split_groups": sorted(
                {group for sample in dataset["samples"] for group in sample.get("split_groups", [])}
            ),
            "observation_ids": [sample["observation_id"] for sample in dataset["samples"]],
            "epochs": epochs,
            "steps": steps,
            "learning_rate": learning_rate,
            "seconds": time.monotonic() - started,
            "device": device,
            "encoder_frozen": True,
            "reviewed_area_scopes": sorted({review_scope(sample) for sample in dataset["samples"]}),
            "scope": "supervised_training; independent evaluation still required",
        },
    )


def evaluation_inputs(
    dataset_dir: Path, model: ModelManifest, *, required_split: str, device: str = "cpu"
):
    from puertogt.ml.detection import detect, load_detector

    path = dataset_dir / "manifest.json"
    dataset = json.loads(path.read_text())
    if dataset.get("split") != required_split or not dataset.get("samples"):
        raise ValueError(f"A nonempty frozen {required_split} split is required")
    training = model.metadata.get("training", {})
    if training.get("dataset_manifest_sha256") == sha256_file(path):
        raise ValueError("Evaluation cannot reuse the training dataset")
    training_groups = set(training.get("split_groups", []))
    training_ids = set(training.get("observation_ids", []))
    detector = load_detector(model, device=device)
    records = []
    for sample in dataset["samples"]:
        if sample["observation_id"] in training_ids or training_groups.intersection(
            sample.get("split_groups", [])
        ):
            raise ValueError("Training and evaluation contain related samples")
        bundle = bundle_from_evidence(sample, dataset_dir)
        references = labels_for_sample(dataset_dir, sample)
        predictions = detect(bundle, model, detector=detector, device=device)
        area = reviewed_area(sample)
        if area is not None:
            predictions = [
                item for item in predictions if area.covers(shape(item.geometry).centroid)
            ]
        records.append(
            {
                "sample": sample,
                "reviewed_area_scope": review_scope(sample),
                "predictions": [p.model_dump(mode="json") for p in predictions],
                "references": references,
            }
        )
    return dataset, records


def evaluate_detector(
    dataset_dir: Path,
    model: ModelManifest,
    *,
    required_strata: list[str],
    min_support: int,
    iou_threshold: float = 0.5,
    device: str = "cpu",
) -> dict:
    from puertogt.ml.evaluation import detection_report, match_detections, publication_decision

    dataset, predictions = evaluation_inputs(
        dataset_dir, model, required_split="test", device=device
    )
    records = []
    for record in predictions:
        sample = record["sample"]
        records.append(
            {
                **{key: sample[key] for key in ("port_id", "zone_id", "sensor")},
                **match_detections(
                    record["predictions"], record["references"], iou_threshold=iou_threshold
                ),
            }
        )
    independent = all(
        sample.get("reference_provenance", {}).get("kind")
        in {"independent_reference", "public_dataset"}
        and sample.get("reference_provenance", {}).get("reference")
        for sample in dataset["samples"]
    )
    report = detection_report(
        records,
        split="test",
        dataset_hash=sha256_file(dataset_dir / "manifest.json"),
        independent_reference=bool(independent),
        test_frozen=True,
    )
    report.update(
        reviewed_area_scopes=sorted({record["reviewed_area_scope"] for record in predictions}),
        model_id=model.model_id,
        weights_sha256=model.weights_sha256,
        score_threshold=model.publication_policy.get("score_threshold", 0.8),
        iou_threshold=iou_threshold,
        publication_decision=publication_decision(
            report, required_strata=required_strata, min_support=min_support
        ),
    )
    return report


def calibrate_detector(
    dataset_dir: Path,
    model: ModelManifest,
    *,
    min_support: int,
    target_precision: float = 0.9,
    iou_threshold: float = 0.5,
    device: str = "cpu",
) -> dict:
    from puertogt.ml.evaluation import match_detections

    if min_support < 1 or not 0 < target_precision <= 1:
        raise ValueError("Explicit support and precision target are required")
    floor = model.model_copy(
        update={"publication_policy": {**model.publication_policy, "score_threshold": 0.0}}
    )
    _, records = evaluation_inputs(dataset_dir, floor, required_split="calibration", device=device)
    candidates = sorted({p["detection_score"] for record in records for p in record["predictions"]})
    eligible = []
    for threshold in candidates:
        tp, fp = 0, 0
        for record in records:
            selected = [p for p in record["predictions"] if p["detection_score"] >= threshold]
            counts = match_detections(selected, record["references"], iou_threshold=iou_threshold)
            tp += counts["true_positives"]
            fp += counts["false_positives"]
        if tp + fp >= min_support and tp / (tp + fp) >= target_precision:
            eligible.append(
                {
                    "score_threshold": threshold,
                    "true_positives": tp,
                    "false_positives": fp,
                    "precision": tp / (tp + fp),
                }
            )
    selected = (
        max(eligible, key=lambda row: (row["true_positives"], -row["score_threshold"]))
        if eligible
        else None
    )
    return {
        "split": "calibration",
        "dataset_hash": sha256_file(dataset_dir / "manifest.json"),
        "selected": selected,
        "target_precision": target_precision,
        "min_support": min_support,
        "status": "test_evaluation_required",
        "weights_sha256": model.weights_sha256,
        "reviewed_area_scopes": sorted({record["reviewed_area_scope"] for record in records}),
    }


def save_report(report: dict, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x") as handle:
        handle.write(json_text(report) + "\n")
    return destination
