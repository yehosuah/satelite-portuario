"""Reproducible supervised workflows exposed by the local CLI.

Training consumes reviewed, frozen labels. Evaluation re-runs inference on a
separate split and never turns a detector score into an accuracy measurement.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import Affine
from rasterio.features import geometry_mask
from shapely.geometry import shape
from shapely.ops import transform as project

from puertogt.contracts import ModelManifest, RasterBundle
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


def reviewed_training_mask(image: np.ndarray, bundle, sample: dict) -> np.ndarray:
    """Freeze supervision coverage while preserving the numeric image context."""
    area = reviewed_area(sample)
    if area is None:
        return np.ones(image.shape[-2:], dtype=bool)
    transformer = Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True)
    inside = geometry_mask(
        [project(transformer.transform, area)],
        out_shape=image.shape[-2:],
        transform=Affine(*bundle.transform),
        invert=True,
    )
    if not inside.any():
        raise ValueError("Frozen reviewed area does not overlap the numeric crop")
    return inside


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
            reviewed_mask = reviewed_training_mask(image, bundle, sample)
            padded = np.pad(image, ((0, 0), (0, (-height) % 32), (0, (-width) % 32)))
            reviewed_mask = np.pad(reviewed_mask, ((0, (-height) % 32), (0, (-width) % 32)))
            steps.append(detector.frozen_train_step(padded, boxes, optimizer=optimizer,
                                                    reviewed_mask=reviewed_mask))
    if steps and all(step.get("skipped") for step in steps):
        raise ValueError("No supervised anchors or proposals; training artifact was not created")
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
            "supervision_policy": "reviewed_footprint_negatives_v1; known positive matches retained; native context preserved",
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


def validate_history_evidence(dataset_dir: Path, dataset: dict, model: ModelManifest) -> dict:
    """Validate complete original historical tiles before model construction."""
    root = Path(dataset_dir).resolve()
    evidence = dataset.get("history_inference", {})
    if evidence.get("version") != "history-tiles-v1" or not evidence.get("acquisitions"):
        raise ValueError("Frozen history-tiles-v1 evidence with original halos is required")
    result = {}
    for acquisition in evidence["acquisitions"]:
        key = acquisition["acquisition_key"]
        if key in result or not acquisition.get("pass_id"):
            raise ValueError("Acquisitions need unique keys and an explicit pass_id")
        entries = acquisition.get("tiles", [])
        ids = [entry["tile"]["tile_id"] for entry in entries]
        if (not ids or len(ids) != len(set(ids))
                or ids != acquisition.get("expected_tile_ids")):
            raise ValueError("Incomplete or reordered original tile set")
        if hashlib.sha256(json_text([e["tile"] for e in entries]).encode()).hexdigest() != acquisition.get("plan_tiles_sha256"):
            raise ValueError("Original plan descriptor checksum mismatch")
        provenance = acquisition.get("provenance", {})
        original = (provenance.get("kind") == "retained_original_prepared_bytes"
                    and provenance.get("original_halo_hash_available") is True)
        replay = (provenance.get("kind") == "reconstructed_from_pinned_sources"
                  and provenance.get("original_halo_hash_available") is False
                  and provenance.get("core_pixel_match_verified") is True)
        if not (original or replay):
            raise ValueError("Explicit retained/reconstructed halo provenance is required")
        if model.sensor == "sentinel-1" and not acquisition.get("context_acquisition_key"):
            raise ValueError("Radar evidence requires its original context acquisition")
        resolved = []
        for entry in entries:
            tile = entry["tile"]
            if (tile.get("size"), tile.get("margin"), tile.get("pixel_size")) != (512, 32, 10):
                raise ValueError("History evaluation requires 512/core448/margin32 at 10 m")
            west, south, east, north = tile["core_bounds"]
            expected = (10, 0, west - 320, 0, -10, north + 320)
            if (not all(math.isfinite(v) for v in (west, south, east, north))
                    or not np.allclose([east - west, north - south], [4480, 4480])
                    or not np.allclose(tile["transform"], expected, rtol=0, atol=1e-8)):
                raise ValueError("Invalid original core bounds or halo transform")
            bundle = RasterBundle.model_validate(entry["bundle"])
            if (bundle.sensor != model.sensor or bundle.preprocessing_version != model.recipe_version
                    or set(bundle.band_paths) != set(model.bands) or bundle.units != model.units
                    or bundle.crs != tile["crs"]
                    or not np.allclose(bundle.transform, expected, rtol=0, atol=1e-8)
                    or bundle.metadata.get("acquisition_key") != key):
                raise ValueError("Original tile does not match acquisition/model/grid")
            if "observable" not in bundle.mask_paths:
                raise ValueError("Original observable mask is required; no quality fallback")
            relative_paths = {str(p) for p in [*bundle.band_paths.values(), *bundle.mask_paths.values()]}
            hashes = entry.get("file_sha256", {})
            if relative_paths != set(hashes):
                raise ValueError("Every original band and mask needs exactly one pinned checksum")
            named_hashes = {name: hashes[str(path)] for name, path in {
                **bundle.band_paths, **bundle.mask_paths}.items()}
            if bundle.metadata.get("history_file_hashes", named_hashes) != named_hashes:
                raise ValueError("Original named materialization checksums disagree")
            paths = {}
            for relative in sorted(relative_paths):
                path = confined_path(root, relative)
                if sha256_file(path) != hashes[relative]:
                    raise ValueError("Original tile checksum mismatch, including halo")
                with rasterio.open(path) as src:
                    if (src.driver not in {"GTiff", "JP2OpenJPEG"} or src.shape != (512, 512)
                            or str(src.crs) != tile["crs"]
                            or not src.transform.almost_equals(Affine(*expected))):
                        raise ValueError("Original numeric tile must preserve its complete 512 grid")
                paths[relative] = path
            resolved.append((tile, bundle.model_copy(update={
                "band_paths": {b: paths[str(p)] for b, p in bundle.band_paths.items()},
                "mask_paths": {b: paths[str(p)] for b, p in bundle.mask_paths.items()},
                "metadata": {**bundle.metadata, "history_file_hashes": named_hashes},
            })))
        result[key] = resolved
    if set(result) != {sample["acquisition_key"] for sample in dataset["samples"]}:
        raise ValueError("Original acquisition set does not match the frozen sample set")
    for sample in dataset["samples"]:
        if not any(sample["zone_id"] in tile["zone_ids"] for tile, _ in result[sample["acquisition_key"]]):
            raise ValueError("Reviewed zone has no original tile coverage")
    return result


def history_pass_id(key: str) -> str:
    """Group adjacent S1 slices or S2 tiles into one physical satellite pass."""
    parts = key.split(":")
    if len(parts) == 8 and parts[0].startswith("S1"):
        return ":".join([parts[0], parts[6], parts[7]])
    if len(parts) == 5 and parts[0].startswith("S2") and parts[1] == "MSIL1C":
        return ":".join([parts[0], parts[2], parts[3]])
    raise ValueError("Canonical acquisition_key is required for independent pass grouping")


def history_evaluation_inputs(dataset_dir: Path, model: ModelManifest, *, required_split: str,
                              cache_dir: Path, detector=None, device: str = "cpu"):
    """Evaluate original halos through the deployed history.infer_tiles path."""
    from puertogt.history import infer_tiles
    from puertogt.ml.detection import load_detector

    root, cache = Path(dataset_dir).resolve(), Path(cache_dir).resolve()
    if root == cache or root in cache.parents or cache in root.parents:
        raise ValueError("Inference cache must be separate from the frozen dataset")
    dataset = json.loads((root / "manifest.json").read_text())
    if required_split not in {"calibration", "test"} or dataset.get("split") != required_split:
        raise ValueError("History evaluation requires its designated calibration or test split")
    tiles_by_acquisition = validate_history_evidence(root, dataset, model)
    training = model.metadata.get("training", {})
    train_ids, train_groups = set(training.get("observation_ids", [])), set(training.get("split_groups", []))
    references = {}
    for sample in dataset["samples"]:
        if (sample["observation_id"] in train_ids or sample.get("parent_observation_id") in train_ids
                or train_groups.intersection([sample["acquisition_key"], *sample.get("split_groups", [])])):
            raise ValueError("Training and evaluation contain related samples")
        if reviewed_area(sample) is None:
            raise ValueError("History evaluation requires an explicit frozen reviewed_area")
        references[sample["observation_id"]] = labels_for_sample(root, sample)
    cache.mkdir(parents=True, exist_ok=False)
    # Build fresh local copies: history.infer_tiles writes its own per-tile cache.
    copied = {}
    for acquisition_index, (key, tiles) in enumerate(tiles_by_acquisition.items()):
        copied[key] = []
        for index, (tile, bundle) in enumerate(tiles):
            directory = cache / str(acquisition_index) / str(index)
            directory.mkdir(parents=True)
            paths = {}
            for number, path in enumerate(sorted(set([*bundle.band_paths.values(), *bundle.mask_paths.values()]))):
                target = directory / f"{number}{path.suffix}"
                shutil.copyfile(path, target)
                if sha256_file(target) != sha256_file(path):
                    raise ValueError("Copied original tile checksum mismatch")
                paths[path] = target
            copied[key].append((tile, bundle.model_copy(update={
                "band_paths": {b: paths[p] for b, p in bundle.band_paths.items()},
                "mask_paths": {b: paths[p] for b, p in bundle.mask_paths.items()},
            })))
    detector = detector or load_detector(model, device=device)
    by_acquisition = {
        key: infer_tiles(tiles, model, detector, device=device) for key, tiles in copied.items()
    }
    records = []
    for sample in dataset["samples"]:
        area = reviewed_area(sample)
        records.append({
            "sample": sample, "reviewed_area_scope": "frozen_area",
            "predictions": [p.model_dump(mode="json") for p in by_acquisition[sample["acquisition_key"]]
                            if area.covers(shape(p.geometry).centroid)],
            "references": references[sample["observation_id"]],
        })
    return dataset, records


def history_detection_report(records: list[dict], *, threshold: float, split: str,
                             min_support: int = 30, min_passes: int = 10,
                             required_ports=("prq", "stc", "pbr")) -> dict:
    """Count objects once across overlapping zones, retaining separate zone metrics."""
    from collections import defaultdict

    from puertogt.ml.evaluation import detection_report, match_detections

    if not 0 <= threshold <= 1 or min_support < 1 or min_passes < 0 or not required_ports:
        raise ValueError("Explicit threshold, support and evaluation scope are required")
    grouped, pass_acquisitions = {}, defaultdict(set)
    zone_counts = []
    for record in records:
        sample = record["sample"]
        port, acquisition, pass_id = sample["port_id"], sample["acquisition_key"], sample["pass_id"]
        pass_acquisitions[port, pass_id].add(acquisition)
        if len(pass_acquisitions[port, pass_id]) > 1:
            raise ValueError("Multiple acquisitions per port/pass need explicit segment adjudication")
        group = grouped.setdefault((port, acquisition), {
            "sensor": sample["sensor"], "pass_id": pass_id, "predictions": {}, "references": {},
        })
        if group["pass_id"] != pass_id or group["sensor"] != sample["sensor"]:
            raise ValueError("Inconsistent acquisition pass or sensor")
        selected = [p for p in record["predictions"] if p["detection_score"] >= threshold]
        for prediction in selected:
            identity = prediction["prediction_id"]
            previous = group["predictions"].get(identity)
            if previous and (previous["geometry"] != prediction["geometry"]
                             or previous["detection_score"] != prediction["detection_score"]):
                raise ValueError("Inconsistent duplicate prediction across zones")
            group["predictions"][identity] = prediction
        for reference in record["references"]:
            identity = reference.get("properties", {}).get("provenance", {}).get("unique_physical_object_key")
            if not identity:
                raise ValueError("References require a frozen unique_physical_object_key")
            previous = group["references"].get(identity)
            if previous and not shape(previous["geometry"]).equals(shape(reference["geometry"])):
                raise ValueError("Duplicate physical object has inconsistent frozen geometry")
            group["references"][identity] = reference
        zone_counts.append({
            **{name: sample[name] for name in ("port_id", "zone_id", "sensor")},
            **match_detections(selected, record["references"], iou_threshold=0.5),
        })
    counts_by_port, passes = defaultdict(list), defaultdict(set)
    for (port, _), group in grouped.items():
        counts_by_port[port].append({
            "port_id": port, "zone_id": "unique_objects", "sensor": group["sensor"],
            **match_detections(list(group["predictions"].values()), list(group["references"].values()),
                               iou_threshold=0.5),
        })
        passes[port].add(group["pass_id"])
    ports = {}
    for port in sorted(set(required_ports) | set(counts_by_port)):
        metric = detection_report(counts_by_port[port], split=split)["overall"]
        reasons = []
        if min(metric["prediction_count"], metric["reference_count"]) < min_support:
            reasons.append("insufficient_support")
        if len(passes[port]) < min_passes:
            reasons.append("insufficient_passes")
        if metric["precision"] is None or metric["precision"] < .9:
            reasons.append("precision_below_target")
        if metric["recall"] is None or metric["recall"] < .8:
            reasons.append("recall_below_target")
        ports[port] = {**metric, "pass_count": len(passes[port]),
                       "eligible": not reasons, "reasons": reasons}
    return {
        "split": split, "threshold": threshold, "iou_threshold": .5,
        "ports": ports, "required_ports": list(required_ports),
        "eligible": all(ports[port]["eligible"] for port in required_ports),
        "overall": detection_report([r for rows in counts_by_port.values() for r in rows], split=split)["overall"],
        "zone_metrics": detection_report(zone_counts, split=split)["strata"],
        "counting_scope": "Unique objects per port/acquisition; overlapping zones are not independent. Overall sums ports.",
        "interval_method": "Wilson95% by object; pass dependence not modeled by that interval",
        "min_support": min_support, "min_passes": min_passes,
        "target_precision": .9, "minimum_recall": .8, "automatic_publication": False,
        "reference_scope": "Agreement with frozen reference provenance; not automatically human validation",
    }


def select_history_threshold(records: list[dict], *, min_support: int = 30,
                             required_ports=("prq", "stc", "pbr")) -> dict:
    """Use the preregistered hundredth grid and per-port precision/recall gates."""
    candidates = [history_detection_report(records, threshold=n / 100, split="calibration",
                                          min_support=min_support, min_passes=0,
                                          required_ports=required_ports) for n in range(10, 100)]
    eligible = [row for row in candidates if row["eligible"]]
    selected = max(eligible, key=lambda row: (row["overall"]["recall"],
                                             row["overall"]["precision"], -row["threshold"])) if eligible else None
    return {"split": "calibration", "selected": selected, "candidates": candidates,
            "status": "test_freeze_required" if selected else "calibration_failed_do_not_open_test",
            "tie_break": "maximum recall, then precision, then lowest threshold; all ports must pass"}
