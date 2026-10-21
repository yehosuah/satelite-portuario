"""Independent test evaluation of attribute heads on reviewed reference objects.

Reference boxes isolate attribute performance from detector recall. This module
neither calibrates on test data nor grants publication permission.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from shapely.geometry import shape

from puertogt.contracts import ModelManifest, Prediction
from puertogt.ml.attributes import FAMILIES, estimate_attributes
from puertogt.ml.evaluation import classification_report, dimension_report
from puertogt.review import bundle_from_evidence
from puertogt.store import confined_path, sha256_file


def _reference(properties: dict[str, Any], field: str) -> tuple[Any, str | None]:
    value = properties.get(field)
    if value is None:
        return None, "missing_reference"
    provenance = properties.get("provenance", {}).get(field, {})
    if (provenance.get("kind") not in {"independent_reference", "public_dataset"}
            or not provenance.get("reference")):
        return None, "missing_independent_provenance"
    if field == "family":
        if value not in FAMILIES:
            return None, "unsupported_reference_family"
    elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Independent dimension references must be positive finite metres")
    return value, None


def _summary(records: list[dict[str, Any]], baseline: dict[str, Any], threshold: float | None) -> dict[str, Any]:
    family_truth = [row["reference"]["family"] for row in records]
    raw_family = [row["estimate"]["family"] for row in records]
    thresholded = [row["estimate"]["family"] if threshold is None or row["estimate"]["family_probability"] >= threshold else None
                   for row in records]
    return {
        "object_count": len(records),
        "scene_count": len({row["observation_id"] for row in records}),
        "family": {"raw": classification_report(family_truth, raw_family),
                   "thresholded": classification_report(family_truth, thresholded),
                   "fixed_probability_threshold": threshold},
        "dimensions": {field: dimension_report(
            [row["reference"][field] for row in records], [row["estimate"][field] for row in records],
            baseline=baseline.get(field),
        ) for field in ("length_m", "width_m")},
        "excluded_references": {field: {
            reason: sum(row["reference_exclusions"].get(field) == reason for row in records)
            for reason in ("missing_reference", "missing_independent_provenance", "unsupported_reference_family")
        } for field in ("family", "length_m", "width_m")},
    }


def evaluate_attributes(dataset_dir: Path | str, model: ModelManifest, *,
                        family_threshold: float | None = None) -> dict[str, Any]:
    """Evaluate a pinned attribute head on the frozen test split only.

    ``family_threshold`` is a fixed, externally chosen threshold; no threshold is
    selected or optimized from this dataset. If omitted, a configured model
    threshold is used, or argmax estimates are reported without abstention.
    Dimensions use raw estimates and the median baseline saved during training.
    """
    root = Path(dataset_dir).resolve()
    manifest_path = confined_path(root, "manifest.json")
    dataset_hash = sha256_file(manifest_path)
    dataset = json.loads(manifest_path.read_text())
    if dataset.get("split") != "test" or not dataset.get("samples"):
        raise ValueError("A nonempty frozen test dataset is required for attribute evaluation")
    if model.task != "attributes" or not model.weights_path or not model.weights_sha256:
        raise ValueError("A trained attribute head with pinned weights is required")
    if sha256_file(Path(model.weights_path)) != model.weights_sha256:
        raise ValueError("Attribute checkpoint checksum mismatch")
    training = model.metadata.get("training", {})
    if training.get("dataset_manifest_sha256") == dataset_hash:
        raise ValueError("Evaluation cannot reuse the training dataset")
    train_groups = set(training.get("split_groups", []))
    train_ids = set(training.get("observation_ids", []))
    if family_threshold is None:
        family_threshold = model.publication_policy.get("fields", {}).get("family", {}).get("min_probability")
        threshold_source = "model_configuration" if family_threshold is not None else "none_argmax_only"
    else:
        threshold_source = "fixed_external_argument"
    if family_threshold is not None and (not math.isfinite(family_threshold) or not 0 <= family_threshold <= 1):
        raise ValueError("Fixed family threshold must be a probability")
    baseline = training.get("dimension_baseline_m", {})
    for value in baseline.values():
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError("Saved training baselines must be positive finite metres")
    seen = set()
    records = []
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scene_counts: dict[str, int] = defaultdict(int)
    for sample in dataset["samples"]:
        if sample.get("split", "test") != "test":
            raise ValueError("Every attribute evaluation sample must belong to test")
        observation_id = sample["observation_id"]
        if observation_id in seen:
            raise ValueError("Repeated observation in test dataset")
        seen.add(observation_id)
        if observation_id in train_ids or train_groups.intersection(sample.get("split_groups", [])):
            raise ValueError("Training and attribute evaluation contain related samples")
        if any(not sample.get(field) for field in ("port_id", "sensor", "zone_id")):
            raise ValueError("Every test sample requires port_id, sensor and zone_id for stratification")
        if sample["sensor"] != model.sensor:
            raise ValueError("Attribute model sensor differs from its evaluation sample")
        label_path = confined_path(root, sample["labels"])
        if sha256_file(label_path) != sample["sha256"]:
            raise ValueError("Reviewed attribute label checksum mismatch")
        features = json.loads(label_path.read_text())["features"]
        detections = []
        reference_ids = set()
        for feature in features:
            geometry = shape(feature["geometry"])
            if geometry.is_empty or not geometry.is_valid or geometry.area <= 0:
                raise ValueError("Reference objects require valid area geometries")
            identity = str(feature["properties"]["id"])
            if identity in reference_ids:
                raise ValueError("Repeated reference object ID in a test scene")
            reference_ids.add(identity)
            # The score is a required schema placeholder, never a detector score for this evaluation.
            detections.append(Prediction(prediction_id=identity, geometry=feature["geometry"], detection_score=1))
        bundle = bundle_from_evidence(sample, root)
        estimates = estimate_attributes(bundle, detections, model)
        if len(estimates) != len(features):
            raise ValueError("Attribute estimates do not cover every reference object")
        key = "|".join(sample[field] for field in ("port_id", "sensor", "zone_id"))
        scene_counts[key] += 1
        strata.setdefault(key, [])
        for feature, estimate in zip(features, estimates):
            if estimate["prediction_id"] != str(feature["properties"]["id"]):
                raise ValueError("Estimate/reference object identity mismatch")
            reference, exclusions = {}, {}
            for field in ("family", "length_m", "width_m"):
                reference[field], exclusions[field] = _reference(feature["properties"], field)
            record = {"observation_id": observation_id, "prediction_id": estimate["prediction_id"],
                      "stratum": key, "reference": reference, "reference_exclusions": exclusions, "estimate": estimate}
            records.append(record)
            strata[key].append(record)
    if sha256_file(manifest_path) != dataset_hash:
        raise ValueError("Test manifest changed during evaluation")
    return {
        "evaluation_scope": "attribute_head_on_reference_objects", "detector_performance_included": False,
        "split": "test", "dataset_version": dataset.get("version"), "dataset_manifest_sha256": dataset_hash,
        "model_id": model.model_id, "model_version": model.version, "weights_sha256": model.weights_sha256,
        "threshold_source": threshold_source, "fixed_family_threshold": family_threshold,
        "threshold_optimized_on_test": False,
        "training_overlap_audit": "checked_recorded_ids_and_groups" if train_ids and train_groups else "incomplete_training_history",
        "dimension_baseline_m": baseline,
        "dimension_baseline_source": "saved_training_labels_only" if any(value is not None for value in baseline.values()) else "unavailable_no_train_reference",
        "overall": {**_summary(records, baseline, family_threshold), "scene_count": len(seen)},
        "strata": {key: {**_summary(rows, baseline, family_threshold), "scene_count": scene_counts[key]}
                   for key, rows in sorted(strata.items())},
        "records": records,
        "publication_decision": {"automatic_publication": False, "status": "evaluation_only",
                                 "reason": "scope_and_field_acceptance_require_separate_review"},
    }
