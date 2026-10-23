"""Numeric eligibility of fixed public references, independent of target labels.

Original selections and evidence remain intact. Missing reference-center data
exclude TRAIN rows from fitting and become explicit evaluation abstentions.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np
import rasterio

from puertogt.history import _atomic_json
from puertogt.preprocess import S2_BANDS
from puertogt.review import bundle_from_evidence
from puertogt.store import confined_path, sha256_file


def numeric_eligibility(bundle) -> dict:
    """Inspect only pinned raster values and the original validity mask."""
    if set(bundle.band_paths) != set(S2_BANDS) or "valid" not in bundle.mask_paths:
        raise ValueError("Numeric eligibility requires nine bands and the original valid mask")
    paths = {**bundle.band_paths, "valid": bundle.mask_paths["valid"]}
    hashes = bundle.metadata.get("history_file_hashes", {})
    for name, path in paths.items():
        if sha256_file(path) != hashes.get(name):
            raise ValueError("Numeric eligibility evidence checksum mismatch")
    with rasterio.open(paths["valid"]) as source:
        mask = source.read(1) > 0
        grid = (source.crs, source.transform, source.shape)
    if mask.shape != (128, 128):
        raise ValueError("Numeric eligibility requires the fixed 128 by 128 context")
    row, column = (size // 2 for size in mask.shape)
    reasons = []
    if not mask.any():
        reasons.append("valid_mask_empty")
    if not mask[row, column]:
        reasons.append("reference_center_unobserved")
    center_values = {}
    for band in S2_BANDS:
        with rasterio.open(paths[band]) as source:
            if (source.crs, source.transform, source.shape) != grid:
                raise ValueError("Numeric eligibility bands and mask are not aligned")
            value = float(source.read(1, window=rasterio.windows.Window(column, row, 1, 1))[0, 0])
            center_values[band] = value if np.isfinite(value) else None
            if not np.isfinite(value):
                reasons.append("reference_center_band_nonfinite:" + band)
            elif source.nodata is not None and value == source.nodata:
                reasons.append("reference_center_band_nodata:" + band)
    return {"eligible": not reasons, "reasons": reasons, "targets_consulted": False,
            "reference_pixel_row_column": [row, column], "reference_center_valid": bool(mask[row, column]),
            "valid_pixels": int(mask.sum()), "context_pixels": int(mask.size),
            "valid_fraction": float(mask.mean()), "center_values": center_values,
            "evidence_sha256": {name: hashes[name] for name in paths}}


def audit_numeric_datasets(dataset_root: Path, selection_sha256: str) -> dict:
    """Audit every selected split without opening labels or model predictions."""
    rows, manifests, seen = [], {}, set()
    for split in ("train", "calibration", "test"):
        root = dataset_root / split
        path = root / "manifest.json"
        manifest = json.loads(path.read_text())
        if manifest.get("selection_sha256") != selection_sha256 or manifest.get("split") != split:
            raise ValueError("Numeric audit selection or split mismatch")
        manifests[split] = sha256_file(path)
        for sample in manifest["samples"]:
            identity = sample["observation_id"]
            if identity in seen or sample.get("split") != split:
                raise ValueError("Numeric audit requires unique references in their original splits")
            seen.add(identity)
            rows.append({"observation_id": identity, "source_window_id": sample["source_window_id"],
                         "split": split, **numeric_eligibility(bundle_from_evidence(sample, root))})
    return {"selection_sha256": selection_sha256, "manifest_sha256": manifests, "rows": rows,
            "summary": {split: {"selected": sum(row["split"] == split for row in rows),
                                "eligible": sum(row["split"] == split and row["eligible"] for row in rows),
                                "ineligible": sum(row["split"] == split and not row["eligible"] for row in rows)}
                        for split in manifests}}


def copy_eligible_dataset(source: Path, target: Path, report: dict, report_sha256: str) -> Path:
    """Create a separate immutable input view; retain the original dataset."""
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    split = manifest["split"]
    if sha256_file(manifest_path) != report["manifest_sha256"][split]:
        raise ValueError("Numeric eligibility source manifest changed")
    rows = {row["observation_id"]: row for row in report["rows"] if row["split"] == split}
    if set(rows) != {sample["observation_id"] for sample in manifest["samples"]}:
        raise ValueError("Numeric eligibility does not cover every selected reference")
    if target.exists():
        raise FileExistsError("Eligible dataset views are immutable")
    shutil.copytree(source, target)
    original = manifest["samples"]
    manifest["samples"] = [sample for sample in original if rows[sample["observation_id"]]["eligible"]]
    manifest["numeric_eligibility"] = {"report_sha256": report_sha256, "selected_sample_count": len(original),
                                       "excluded_observation_ids": [sample["observation_id"] for sample in original
                                                                    if not rows[sample["observation_id"]]["eligible"]]}
    _atomic_json(target / "manifest.json", manifest)
    return target


def evaluate_with_numeric_abstentions(original: Path, eligible: Path, model, report: dict) -> dict:
    """Evaluate once and restore numeric abstentions to every metric denominator."""
    from puertogt.ml.attribute_workflows import _reference, _summary, evaluate_attributes

    manifest_path = original / "manifest.json"
    if sha256_file(manifest_path) != report["manifest_sha256"]["test"]:
        raise ValueError("Original test manifest changed after numeric eligibility freeze")
    manifest = json.loads(manifest_path.read_text())
    rows = {row["observation_id"]: row for row in report["rows"] if row["split"] == "test"}
    if set(rows) != {sample["observation_id"] for sample in manifest["samples"]}:
        raise ValueError("Numeric eligibility must cover the entire original test set")
    subset = json.loads((eligible / "manifest.json").read_text())
    if {sample["observation_id"] for sample in subset["samples"]} != {key for key, row in rows.items() if row["eligible"]}:
        raise ValueError("Eligible test references differ from the frozen numeric audit")
    training = model.metadata.get("training", {})
    for sample in manifest["samples"]:
        if (sample["observation_id"] in training.get("observation_ids", [])
                or set(sample.get("split_groups", [])).intersection(training.get("split_groups", []))):
            raise ValueError("Training and original test contain related references")
    if subset["samples"]:
        result = evaluate_attributes(eligible, model)
    else:
        if not model.weights_path or sha256_file(Path(model.weights_path)) != model.weights_sha256:
            raise ValueError("Attribute checkpoint checksum mismatch")
        result = {"records": [], "split": "test", "model_id": model.model_id,
                  "model_version": model.version, "weights_sha256": model.weights_sha256,
                  "fixed_family_threshold": None, "threshold_optimized_on_test": False,
                  "dimension_baseline_m": model.metadata.get("training", {}).get("dimension_baseline_m", {}),
                  "publication_decision": {"automatic_publication": False, "status": "evaluation_only"}}
    for sample in manifest["samples"]:
        if rows[sample["observation_id"]]["eligible"]:
            continue
        labels = confined_path(original, sample["labels"])
        if sha256_file(labels) != sample["sha256"]:
            raise ValueError("Abstained reference label checksum mismatch")
        for feature in json.loads(labels.read_text())["features"]:
            reference, exclusions = {}, {}
            for field in ("family", "length_m", "width_m"):
                reference[field], exclusions[field] = _reference(feature["properties"], field)
            identity = str(feature["properties"]["id"])
            result["records"].append({"observation_id": sample["observation_id"], "prediction_id": identity,
                "stratum": "|".join(sample[field] for field in ("port_id", "sensor", "zone_id")),
                "reference": reference, "reference_exclusions": exclusions,
                "estimate": {"prediction_id": identity, "family": None, "family_probability": 0.0,
                             "length_m": None, "width_m": None},
                "numeric_abstention_reasons": rows[sample["observation_id"]]["reasons"]})
    strata = defaultdict(list)
    for record in result["records"]:
        strata[record["stratum"]].append(record)
    baseline, threshold = result["dimension_baseline_m"], result["fixed_family_threshold"]
    result.update(overall=_summary(result["records"], baseline, threshold),
                  strata={key: _summary(records, baseline, threshold) for key, records in sorted(strata.items())},
                  numeric_coverage=report["summary"]["test"],
                  original_dataset_manifest_sha256=report["manifest_sha256"]["test"],
                  numeric_abstentions_included_in_denominators=True)
    return result
