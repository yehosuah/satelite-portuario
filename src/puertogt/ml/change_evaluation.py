"""Frozen-test evaluation of the actual postprocessed change polygons.

Consumes the pair format documented in change_workflows, with split=test and
frozen=true. Every sample must declare port_id, zone_id, sensor=sentinel-2 and
labels.review_kind=human. The existing model threshold and minimum-area filter
are used unchanged. Results describe segmentation and false alarms on negative
pairs; they do not certify temporal persistence or grant publication approval.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from pyproj import CRS, Geod, Transformer
from rasterio import Affine
from rasterio.features import rasterize, shapes
from shapely.geometry import shape
from shapely.ops import transform

from puertogt.contracts import ModelManifest, RasterBundle
from puertogt.ml.change import compare
from puertogt.ml.change_workflows import (
    PAIR_FORMAT,
    _groups,
    _load_bundle,
    _reviewed_pair,
    _verified,
)
from puertogt.store import confined_path, sha256_file


def _test_manifest(root: Path, model: ModelManifest) -> dict:
    path = confined_path(root, "manifest.json")
    manifest = json.loads(path.read_text())
    if (
        manifest.get("format") != PAIR_FORMAT
        or manifest.get("split") != "test"
        or manifest.get("frozen") is not True
    ):
        raise ValueError("Change evaluation requires a frozen reviewed-pairs test manifest")
    if not manifest.get("version") or not manifest.get("samples"):
        raise ValueError("A versioned nonempty frozen test set is required")
    training = model.metadata.get("training", {})
    memberships = {}
    for field in ("split_groups", "acquisition_ids", "observation_ids"):
        values = training.get(field)
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(value, str) and value for value in values)
        ):
            raise ValueError("Training split membership is required before independent evaluation")
        memberships[field] = set(values)
    if not re.fullmatch(r"[a-f0-9]{64}", str(training.get("dataset_manifest_sha256", ""))):
        raise ValueError("A pinned training dataset manifest hash is required")
    ids, pairs = set(), set()
    for sample in manifest["samples"]:
        identity = sample.get("pair_id")
        _groups(sample)
        pair = tuple(sample[role]["acquisition_key"] for role in ("reference", "target"))
        if not identity or identity in ids or pair in pairs:
            raise ValueError("Test pair IDs and acquisition pairs must be unique")
        ids.add(identity)
        pairs.add(pair)
        if sample.get("split", "test") != "test":
            raise ValueError("Every evaluated pair must belong to test")
        if (
            not all(
                isinstance(sample.get(field), str) and sample[field].strip()
                for field in ("port_id", "zone_id")
            )
            or sample.get("sensor") != "sentinel-2"
        ):
            raise ValueError("Every test pair needs explicit port_id, zone_id and optical sensor")
        if sample.get("labels", {}).get("review_kind") != "human":
            raise ValueError("Test references require attributed human review")
        if (
            memberships["split_groups"].intersection(sample["split_groups"])
            or memberships["acquisition_ids"].intersection(pair)
            or memberships["observation_ids"].intersection(
                sample[role]["observation_id"] for role in ("reference", "target")
            )
        ):
            raise ValueError("Training/test leakage: related group, acquisition or observation")
        # Validate all labels and numeric inputs before any inference is started.
        _reviewed_pair(root, sample, manifest.get("quality_protocol", {}))
    return {**manifest, "manifest_sha256": sha256_file(path)}


def _area_m2(mask: np.ndarray, bundle: RasterBundle) -> float:
    if not mask.any():
        return 0.0
    crs, affine = CRS.from_user_input(bundle.crs), Affine(*bundle.transform)
    if crs.is_projected and len(crs.axis_info) >= 2:
        factors = [axis.unit_conversion_factor for axis in crs.axis_info[:2]]
        if all(value is not None and math.isfinite(value) and value > 0 for value in factors):
            return float(
                mask.sum()
                * abs(affine.a * affine.e - affine.b * affine.d)
                * factors[0]
                * factors[1]
            )
    project = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    geod, area = Geod(ellps="WGS84"), 0.0
    for polygon, _ in shapes(mask.astype("uint8"), mask=mask, transform=affine):
        geometry = transform(project.transform, shape(polygon))
        value, _ = geod.geometry_area_perimeter(geometry)
        area += abs(value)
    return float(area)


def _summary(counts: dict) -> dict:
    tp, fp, fn = (
        counts.get(field, 0)
        for field in ("true_positive_pixels", "false_positive_pixels", "false_negative_pixels")
    )
    tp_area, fp_area, fn_area = (
        counts.get(field, 0.0)
        for field in ("true_positive_area_m2", "false_positive_area_m2", "false_negative_area_m2")
    )
    negative = counts.get("negative_pairs", 0)
    return {
        **counts,
        "iou": tp / (tp + fp + fn) if tp + fp + fn else None,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "area_iou": tp_area / (tp_area + fp_area + fn_area)
        if tp_area + fp_area + fn_area
        else None,
        "negative_pair_false_alarm_fraction": counts.get("negative_pairs_with_false_alarm", 0)
        / negative
        if negative
        else None,
        "false_positive_area_m2_per_negative_pair": counts.get(
            "negative_false_positive_area_m2", 0.0
        )
        / negative
        if negative
        else None,
    }


def evaluate_change_dataset(dataset_dir: Path | str, model: ModelManifest) -> dict:
    """Evaluate frozen human masks against rasterized candidates, without tuning or approval.

    Models produced before training membership was recorded must be supplied with
    their audited original memberships; an absent membership list fails closed.
    The return value is JSON serializable and does not mutate the model manifest.
    """
    if model.task != "change" or model.sensor != "sentinel-2":
        raise ValueError("An optical change model is required")
    threshold = model.publication_policy.get("change_threshold")
    if threshold is None or not math.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("The model must already define its fixed change threshold")
    if not model.weights_path or not model.weights_sha256:
        raise ValueError("Evaluation requires trained, checksum-pinned model weights")
    if sha256_file(Path(model.weights_path)) != model.weights_sha256:
        raise ValueError("Change evaluation weight checksum mismatch")
    root = Path(dataset_dir).resolve()
    dataset = _test_manifest(root, model)
    strata, total, records = defaultdict(lambda: defaultdict(int)), defaultdict(int), []
    for sample in dataset["samples"]:
        reference = _load_bundle(root, sample["reference"], sample["asset_hashes"])
        target = _load_bundle(root, sample["target"], sample["asset_hashes"])
        _, _, labels, valid = _reviewed_pair(root, sample, dataset["quality_protocol"])
        events = compare(reference, target, model)
        project = Transformer.from_crs("EPSG:4326", target.crs, always_xy=True)
        polygons = []
        for event in events:
            if (
                event.model_id != model.model_id
                or event.reference_observation_id != reference.observation_id
                or event.first_observation_id != target.observation_id
                or event.status != "candidate"
            ):
                raise ValueError("Compared candidates do not match the evaluated model and pair")
            geometry = shape(event.geometry)
            if (
                geometry.is_empty
                or not geometry.is_valid
                or geometry.geom_type not in {"Polygon", "MultiPolygon"}
            ):
                raise ValueError("Change predictions must be valid area geometries")
            polygons.append(transform(project.transform, geometry))
        predicted = (
            rasterize(
                ((geometry, 1) for geometry in polygons),
                out_shape=valid.shape,
                transform=Affine(*target.transform),
                fill=0,
                dtype="uint8",
            ).astype(bool)
            if polygons
            else np.zeros(valid.shape, dtype=bool)
        )
        predicted &= valid
        truth = (labels == 1) & valid
        tp, fp, fn, tn = (
            predicted & truth,
            predicted & ~truth,
            ~predicted & truth,
            ~predicted & ~truth & valid,
        )
        negative = sample["labels"]["negative"]
        fp_area = _area_m2(fp, target)
        counts = {
            "pairs": 1,
            "positive_pairs": int(not negative),
            "negative_pairs": int(negative),
            "valid_pixels": int(valid.sum()),
            "true_positive_pixels": int(tp.sum()),
            "false_positive_pixels": int(fp.sum()),
            "false_negative_pixels": int(fn.sum()),
            "true_negative_pixels": int(tn.sum()),
            "candidate_events": len(events),
            "valid_area_m2": _area_m2(valid, target),
            "true_positive_area_m2": _area_m2(tp, target),
            "false_positive_area_m2": fp_area,
            "false_negative_area_m2": _area_m2(fn, target),
            "negative_pairs_with_false_alarm": int(negative and fp.any()),
            "negative_false_positive_area_m2": fp_area if negative else 0.0,
        }
        key = "|".join(sample[field] for field in ("port_id", "sensor", "zone_id"))
        for field, value in counts.items():
            strata[key][field] += value
            total[field] += value
        records.append(
            {
                "pair_id": sample["pair_id"],
                "port_id": sample["port_id"],
                "zone_id": sample["zone_id"],
                "sensor": sample["sensor"],
                "metrics": _summary(counts),
                "candidates": [event.model_dump(mode="json") for event in events],
            }
        )
        for role in ("reference", "target"):
            _verified(root, sample[role]["bundle"], sample[role]["sha256"])
        _verified(root, sample["labels"]["path"], sample["labels"]["sha256"])
        for path, digest in sample["asset_hashes"].items():
            _verified(root, path, digest)
    if sha256_file(root / "manifest.json") != dataset["manifest_sha256"]:
        raise ValueError("Test manifest changed during evaluation")
    return {
        "task": "change_segmentation",
        "split": "test",
        "test_frozen": True,
        "dataset_version": dataset["version"],
        "dataset_manifest_sha256": dataset["manifest_sha256"],
        "model_id": model.model_id,
        "model_version": model.version,
        "weights_sha256": model.weights_sha256,
        "training_manifest_sha256": model.metadata["training"]["dataset_manifest_sha256"],
        "threshold": threshold,
        "min_area_m2": model.publication_policy.get("min_area_m2"),
        "threshold_selection": "fixed_from_model_manifest; no threshold tuning on test",
        "automatic_validation": False,
        "overall": _summary(dict(total)),
        "strata": {key: _summary(dict(values)) for key, values in sorted(strata.items())},
        "pairs": records,
        "limitations": [
            "Pixel metrics are spatially dependent; no independent-event confidence interval is claimed.",
            "Candidates on pairs do not validate temporal persistence.",
            "Missing strata remain unevaluated; this report does not authorize publication.",
        ],
    }
