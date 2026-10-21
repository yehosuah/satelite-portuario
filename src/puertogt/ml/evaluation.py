"""Metrics from independent references, calibration isolation and publication gates."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


def _counts(**counts: int) -> None:
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
        raise ValueError("Counts must be nonnegative integers")


def precision_at_counts(true_positives: int, false_positives: int) -> float | None:
    _counts(true_positives=true_positives, false_positives=false_positives)
    denominator = true_positives + false_positives
    return true_positives / denominator if denominator else None


def wilson_interval(successes: int, total: int, *, z: float = 1.959963984540054) -> list[float] | None:
    _counts(successes=successes, total=total)
    if successes > total or not math.isfinite(z) or z <= 0:
        raise ValueError("Invalid binomial interval arguments")
    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [max(0, center - spread), min(1, center + spread)]


def match_detections(predictions: Sequence[Mapping[str, Any]], references: Sequence[Mapping[str, Any]],
                     *, iou_threshold: float = 0.5) -> dict[str, int]:
    """Greedy, score-ordered one-to-one polygon matching within one complete scene."""
    from shapely.geometry import shape

    if not 0 < iou_threshold <= 1:
        raise ValueError("IoU threshold must be in (0, 1]")
    truth = [shape(value["geometry"]) for value in references]
    predicted = [shape(value["geometry"]) for value in predictions]
    if any(value.is_empty or not value.is_valid or value.area <= 0 for value in [*truth, *predicted]):
        raise ValueError("IoU matching requires valid area geometries")
    unmatched = set(range(len(truth)))
    true_positives = 0
    for index in sorted(range(len(predictions)), key=lambda n: predictions[n].get("detection_score", 0), reverse=True):
        geometry = predicted[index]
        candidates = [(geometry.intersection(truth[ref]).area / geometry.union(truth[ref]).area, ref)
                      for ref in unmatched]
        if candidates:
            iou, reference = max(candidates)
            if iou >= iou_threshold:
                unmatched.remove(reference)
                true_positives += 1
    return {"true_positives": true_positives, "false_positives": len(predictions) - true_positives,
            "false_negatives": len(unmatched)}


def _summary(tp: int, fp: int, fn: int, scenes: int) -> dict[str, Any]:
    precision = precision_at_counts(tp, fp)
    recall = tp / (tp + fn) if tp + fn else None
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "prediction_count": tp + fp, "reference_count": tp + fn, "scene_count": scenes,
            "precision": precision, "recall": recall,
            "precision_interval": wilson_interval(tp, tp + fp), "recall_interval": wilson_interval(tp, tp + fn),
            "false_positives_per_scene": fp / scenes if scenes else None}


def detection_report(records: Iterable[Mapping[str, Any]], *, split: str = "test",
                     dataset_hash: str | None = None, independent_reference: bool = False,
                     test_frozen: bool = False) -> dict[str, Any]:
    if split not in {"train", "calibration", "test"}:
        raise ValueError("Invalid evaluation split")
    strata = defaultdict(lambda: [0, 0, 0, 0])
    total = [0, 0, 0, 0]
    for record in records:
        counts = [record[name] for name in ("true_positives", "false_positives", "false_negatives")]
        _counts(**dict(zip(("true_positives", "false_positives", "false_negatives"), counts)))
        key = "|".join(str(record[field]) for field in ("port_id", "sensor", "zone_id"))
        for index, count in enumerate([*counts, 1]):
            strata[key][index] += count
            total[index] += count
    return {"split": split, "dataset_hash": dataset_hash, "independent_reference": independent_reference,
            "test_frozen": test_frozen, "interval_method": "Wilson 95%; clustered scene dependence not modeled",
            "overall": _summary(*total), "strata": {key: _summary(*value) for key, value in sorted(strata.items())}}


def publication_decision(report: Mapping[str, Any], *, required_strata: Iterable[str], min_support: int,
                         target_precision: float = 0.9,
                         minimum_lower_bound: float | None = None) -> dict[str, Any]:
    """Explicitly scoped validation; support thresholds must be set by the protocol."""
    required = list(required_strata)
    if not required or min_support < 1 or not 0 < target_precision <= 1:
        raise ValueError("An explicit scope, positive support and precision target are required")
    reasons = []
    if report.get("split") != "test" or report.get("test_frozen") is not True:
        reasons.append("frozen_test_required")
    if report.get("independent_reference") is not True:
        reasons.append("independent_reference_required")
    if not re.fullmatch(r"[a-f0-9]{64}", str(report.get("dataset_hash", ""))):
        reasons.append("dataset_hash_required")
    strata = {}
    for key in required:
        metric = report.get("strata", {}).get(key)
        failures = []
        if metric is None:
            failures.append("missing_stratum")
        else:
            if min(metric["prediction_count"], metric["reference_count"]) < min_support:
                failures.append("insufficient_support")
            if metric["precision"] is None or metric["precision"] < target_precision:
                failures.append("precision_below_target")
            if minimum_lower_bound is not None and (metric["precision_interval"] is None
                                                    or metric["precision_interval"][0] < minimum_lower_bound):
                failures.append("precision_lower_bound_below_target")
        strata[key] = {"eligible": not failures and not reasons, "reasons": failures, "metrics": metric}
    reasons.extend(reason for scope in strata.values() for reason in scope["reasons"])
    eligible = not reasons
    return {"status": "validated" if eligible else "experimental", "automatic_publication": eligible,
            "reasons": sorted(set(reasons)), "strata": strata, "required_strata": required,
            "min_support": min_support, "target_precision": target_precision,
            "minimum_lower_bound": minimum_lower_bound,
            "scope": "detection_only; family, dimensions and changes require independent gates"}


def calibrate_threshold(records: Sequence[Mapping[str, Any]], *, split: str,
                        target_precision: float, min_predictions: int) -> dict[str, Any]:
    if split != "calibration":
        raise ValueError("Threshold selection is restricted to the calibration split")
    if not 0 < target_precision <= 1 or min_predictions < 1:
        raise ValueError("Invalid calibration target or support")
    if any(not math.isfinite(float(item["score"])) or not 0 <= item["score"] <= 1 for item in records):
        raise ValueError("Scores must be finite probabilities")
    candidates = []
    for threshold in sorted({float(row["score"]) for row in records}):
        selected = [row for row in records if row["score"] >= threshold]
        tp = sum(bool(row["matched"]) for row in selected)
        precision = tp / len(selected)
        if len(selected) >= min_predictions and precision >= target_precision:
            candidates.append({"threshold": threshold, "precision": precision, "predictions": len(selected),
                               "true_positives": tp, "split": split})
    return max(candidates, key=lambda item: (item["true_positives"], -item["threshold"])) if candidates else {
        "threshold": None, "split": split, "reason": "no_supported_threshold_meets_target"}


def classification_report(truth: Sequence[str | None], predictions: Sequence[str | None]) -> dict[str, Any]:
    if len(truth) != len(predictions):
        raise ValueError("Reference and prediction lengths differ")
    pairs = [(actual, prediction) for actual, prediction in zip(truth, predictions) if actual is not None]
    labels = sorted({actual for actual, _ in pairs} | {prediction for _, prediction in pairs if prediction is not None})
    confusion = {actual: {prediction: 0 for prediction in [*labels, "indeterminado"]} for actual in labels}
    for actual, prediction in pairs:
        confusion[actual][prediction if prediction is not None else "indeterminado"] += 1
    per_class = {}
    for label in labels:
        tp = sum(actual == label and predicted == label for actual, predicted in pairs)
        fp = sum(actual != label and predicted == label for actual, predicted in pairs)
        fn = sum(actual == label and predicted != label for actual, predicted in pairs)
        per_class[label] = {"precision": tp / (tp + fp) if tp + fp else None,
                            "recall": tp / (tp + fn) if tp + fn else None,
                            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
                            "support": tp + fn}
    f1s = [value["f1"] for value in per_class.values() if value["f1"] is not None]
    return {"reference_count": len(pairs), "abstention_fraction": sum(p is None for _, p in pairs) / len(pairs) if pairs else None,
            "confusion_matrix": confusion, "per_class": per_class, "macro_f1": sum(f1s) / len(f1s) if f1s else None}


def dimension_report(truth: Sequence[float | None], predictions: Sequence[float | None], *, baseline: float | None = None) -> dict[str, Any]:
    if len(truth) != len(predictions):
        raise ValueError("Reference and prediction lengths differ")
    if any(value is not None and (not math.isfinite(value) or value <= 0) for value in [*truth, *predictions]):
        raise ValueError("Dimensions must be positive finite metres or null")
    reference_count = sum(value is not None for value in truth)
    pairs = [(actual, estimate) for actual, estimate in zip(truth, predictions) if actual is not None and estimate is not None]
    errors = [estimate - actual for actual, estimate in pairs]
    return {"reference_count": reference_count, "estimated_count": len(pairs),
            "coverage": len(pairs) / reference_count if reference_count else None,
            "mae_m": sum(abs(value) for value in errors) / len(errors) if errors else None,
            "bias_m": sum(errors) / len(errors) if errors else None,
            "baseline_mae_m": sum(abs(baseline - actual) for actual, _ in pairs) / len(pairs) if pairs and baseline is not None else None,
            "baseline_comparison": "same reference subset; baseline fixed from training only"}
