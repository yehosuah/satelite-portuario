"""Explain frozen TRAIN/CAL failures without running a model or opening TEST.

This is a post-hoc diagnostic, not a new evaluation or a threshold search. The
two inputs are fixed, completed phase-2 experiments. IoU and score remain 0.50.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape

from puertogt.ml.workflows import history_detection_report
from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "train": ROOT / "data/experiments/phase2-s2-train-temporal-v1-20261022"
    / "final-training-fit/predictions.json",
    "calibration": ROOT / "data/experiments/phase2-s2-candidate-calibration-v1-20261023"
    / "calibration/predictions.json",
}
COUNTS = ("true_positives", "false_positives", "false_negatives")


def distribution(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"count": len(values), "minimum": min(values),
            "median": statistics.median(values), "maximum": max(values)}


def grouped_records(records: list[dict]) -> dict:
    grouped = {}
    for record in records:
        sample = record["sample"]
        group = grouped.setdefault((sample["port_id"], sample["acquisition_key"]), {
            "pass_id": sample["pass_id"], "predictions": {}, "references": {},
            "reference_zones": defaultdict(set),
        })
        for prediction in record["predictions"]:
            if prediction["detection_score"] >= .5:
                group["predictions"][prediction["prediction_id"]] = prediction
        for reference in record["references"]:
            identity = reference["properties"]["provenance"]["unique_physical_object_key"]
            group["references"][identity] = reference
            group["reference_zones"][identity].add(sample["zone_id"])
    return grouped


def diagnose(records: list[dict], *, split: str) -> dict:
    if split not in {"train", "calibration"}:
        raise ValueError("Only completed TRAIN or CAL diagnostics are permitted; TEST stays closed")
    # The production report validates duplicate identities and segment contracts.
    baseline = history_detection_report(records, threshold=.5, split=split, min_passes=0)
    ports, numbers, errors, reference_rows = defaultdict(Counter), defaultdict(
        lambda: defaultdict(list)), [], []
    for (port, acquisition), group in grouped_records(records).items():
        predictions = list(group["predictions"].values())
        reference_ids = list(group["references"])
        references = list(group["references"].values())
        predicted = [shape(item["geometry"]) for item in predictions]
        truth = [shape(item["geometry"]) for item in references]
        unmatched = set(range(len(truth)))
        matched_predictions = set()
        matrix = [[geometry.intersection(reference).area / geometry.union(reference).area
                   for reference in truth] for geometry in predicted]
        for index in sorted(range(len(predictions)), key=lambda i: predictions[i]["detection_score"],
                            reverse=True):
            candidates = [(matrix[index][ref], ref) for ref in unmatched]
            if candidates:
                iou, ref = max(candidates)
                if iou >= .5:
                    unmatched.remove(ref)
                    matched_predictions.add(index)
        counts = ports[port]
        counts.update(true_positives=len(matched_predictions),
                      false_positives=len(predictions) - len(matched_predictions),
                      false_negatives=len(unmatched), references=len(truth),
                      predictions=len(predictions), port_acquisitions=1)
        projection = Transformer.from_crs(4326, 32615 if port == "prq" else 32616,
                                          always_xy=True)
        for index, geometry in enumerate(truth):
            bounds = geometry.bounds
            corners = [projection.transform(x, y) for x, y in (
                (bounds[0], bounds[1]), (bounds[2], bounds[1]),
                (bounds[2], bounds[3]), (bounds[0], bounds[3]))]
            width = max(x for x, _ in corners) - min(x for x, _ in corners)
            height = max(y for _, y in corners) - min(y for _, y in corners)
            reference_rows.append({"port_id": port, "acquisition_key": acquisition,
                                   "reference_id": reference_ids[index],
                                   "zone_ids": sorted(group["reference_zones"][reference_ids[index]]),
                                   "visual_wgs84_envelope_utm_width_m": width,
                                   "visual_wgs84_envelope_utm_height_m": height,
                                   "physical_vessel_dimensions": False})
        for index in sorted(set(range(len(predictions))) - matched_predictions):
            best_iou, ref = max(((value, i) for i, value in enumerate(matrix[index])),
                               default=(0., None))
            overlaps = sum(value > 0 for value in matrix[index])
            category = ("false_positive_with_reference_overlap" if best_iou > 0
                        else "false_positive_without_reference_overlap")
            counts[category] += 1
            row = {"kind": "false_positive", "port_id": port, "acquisition_key": acquisition,
                   "prediction_id": predictions[index]["prediction_id"],
                   "score": predictions[index]["detection_score"], "category": category,
                   "overlapping_references": overlaps, "best_reference_iou": best_iou,
                   "nearest_overlapping_reference_id": None, "centroid_distance_m": None,
                   "predicted_to_reference_area_ratio": None}
            if best_iou > 0:
                x1, y1 = projection.transform(predicted[index].centroid.x,
                                              predicted[index].centroid.y)
                x2, y2 = projection.transform(truth[ref].centroid.x, truth[ref].centroid.y)
                distance = ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** .5
                ratio = predicted[index].area / truth[ref].area
                row.update(nearest_overlapping_reference_id=reference_ids[ref],
                           nearest_overlapping_reference_is_unmatched=ref in unmatched,
                           centroid_distance_m=distance, predicted_to_reference_area_ratio=ratio)
                numbers[port]["overlapping_fp_best_iou"].append(best_iou)
                numbers[port]["overlapping_fp_centroid_distance_m"].append(distance)
                numbers[port]["overlapping_fp_area_ratio"].append(ratio)
            errors.append(row)
        for ref in sorted(unmatched):
            best = max((values[ref] for values in matrix), default=0.)
            category = ("false_negative_with_prediction_overlap" if best > 0
                        else "false_negative_without_prediction_overlap")
            counts[category] += 1
            errors.append({"kind": "false_negative", "port_id": port,
                           "acquisition_key": acquisition, "reference_id": reference_ids[ref],
                           "category": category, "best_prediction_iou": best})
    for port, counts in ports.items():
        if any(counts[key] != baseline["ports"][port][key] for key in COUNTS):
            raise ValueError("Diagnostic counts differ from the frozen evaluation contract")
    total = Counter()
    for counts in ports.values():
        total.update(counts)
    return {"split": split, "score_threshold": .5, "iou_threshold": .5,
            "strict_metrics": {"overall": baseline["overall"], "ports": baseline["ports"]},
            "counts": {"overall": dict(total), "ports": dict(ports)},
            "geometry_distributions": {port: {name: distribution(values)
                for name, values in fields.items()} for port, fields in numbers.items()},
            "errors": errors, "reference_geometry": reference_rows,
            "counts_reconciled": True, "local_validation_demonstrated": False}


def run(destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError("Use a new immutable diagnostic output")
    pins = {split: {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
            for split, path in INPUTS.items()}
    results = {split: diagnose(json.loads(path.read_text()), split=split)
               for split, path in INPUTS.items()}
    # Reading prior results must not silently mutate them.
    if any(sha256_file(INPUTS[split]) != pin["sha256"] for split, pin in pins.items()):
        raise ValueError("A frozen input changed during the diagnostic")
    result = {"schema_version": "phase2-frozen-localization-diagnostic-v1",
              "created_at": utc_text(), "input_pins": pins,
              "script_sha256": sha256_file(Path(__file__)), "results": results,
              "scope": "Post-hoc geometric diagnosis of completed TRAIN/CAL, not validation",
              "limits": ["No model forward, optimizer, threshold search or TEST access.",
                         "Overlap is a geometric association, not proof of vessel identity.",
                         "Near-miss boxes remain false positives and false negatives at IoU0.50.",
                         "One prediction can overlap several references; categories are not paired counts.",
                         "Visual boxes do not provide physical vessel dimensions.",
                         "Labels remain attributed AI references on explicit, partly detector-conditioned areas.",
                         "A development decision informed by CAL needs a new frozen calibration cohort."],
              "test_opened": False, "local_validation_demonstrated": False,
              "automatic_publication": False}
    destination.mkdir(parents=True)
    (destination / "localization.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = run(args.output)
    print(json.dumps({split: result["counts"] for split, result in output["results"].items()}, indent=2))
