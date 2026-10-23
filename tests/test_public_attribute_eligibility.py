import json
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio import Affine

from puertogt.contracts import RasterBundle
from puertogt.ml.public_attribute_eligibility import numeric_eligibility
from puertogt.preprocess import S2_BANDS
from puertogt.store import sha256_file


def numeric_bundle(tmp_path, *, valid_center=True, valid_pixels=True, missing_band=None, center_value=0):
    paths, masks = {}, {}
    valid = np.ones((128, 128), dtype=np.uint8) if valid_pixels else np.zeros((128, 128), dtype=np.uint8)
    if not valid_center:
        valid[64, 64] = 0
    for name in [*S2_BANDS, "valid"]:
        values = valid if name == "valid" else np.full((128, 128), 1000, dtype=np.float32)
        if name == missing_band:
            values[64, 64] = center_value
        path = tmp_path / (name + ".tif")
        with rasterio.open(path, "w", driver="GTiff", count=1, width=128, height=128,
                           dtype=values.dtype, transform=Affine(10, 0, 100, 0, -10, 2000),
                           crs="EPSG:32616", nodata=0) as dst:
            dst.write(values, 1)
        (masks if name == "valid" else paths)[name] = path
    return RasterBundle(sensor="sentinel-2", product_level="L1C",
                        band_paths=paths, mask_paths=masks, crs="EPSG:32616",
                        transform=[10, 0, 100, 0, -10, 2000], preprocessing_version="ai2-s2-v1",
                        units={name: "DN_harmonized" for name in S2_BANDS},
                        metadata={"history_file_hashes": {name: sha256_file(path) for name, path in
                                                         {**paths, **masks}.items()}})


@pytest.mark.parametrize("condition,reason", [
    ({"valid_pixels": False}, "valid_mask_empty"),
    ({"valid_center": False}, "reference_center_unobserved"),
    ({"missing_band": "B12"}, "reference_center_band_nodata:B12"),
    ({"missing_band": "B12", "center_value": float("nan")}, "reference_center_band_nonfinite:B12"),
])
def test_missing_numeric_reference_is_ineligible_without_labels(tmp_path, condition, reason):
    result = numeric_eligibility(numeric_bundle(tmp_path, **condition))
    assert result["eligible"] is False
    assert reason in result["reasons"]
    assert result["targets_consulted"] is False


def test_partial_context_is_retained_when_reference_center_is_valid(tmp_path):
    bundle = numeric_bundle(tmp_path)
    path = bundle.mask_paths["valid"]
    with rasterio.open(path, "r+") as dst:
        values = dst.read(1)
        values[:16] = 0
        dst.write(values, 1)
    bundle.metadata["history_file_hashes"]["valid"] = sha256_file(path)
    result = numeric_eligibility(bundle)
    assert result["eligible"] is True
    assert result["valid_fraction"] == 0.875


def test_numeric_quality_checks_hashes_before_interpreting_pixels(tmp_path):
    bundle = numeric_bundle(tmp_path)
    bundle.metadata["history_file_hashes"]["B04"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        numeric_eligibility(bundle)


def test_all_split_audit_does_not_open_labels_and_retains_original_ids(tmp_path, monkeypatch):
    from puertogt.ml.public_attribute_eligibility import (
        audit_numeric_datasets,
        copy_eligible_dataset,
    )

    assets = tmp_path / "assets"
    assets.mkdir()
    bundle = numeric_bundle(assets, valid_center=False)
    monkeypatch.setattr("puertogt.ml.public_attribute_eligibility.bundle_from_evidence", lambda *_: bundle)
    for split in ("train", "calibration", "test"):
        root = tmp_path / split
        root.mkdir()
        sample = {"observation_id": split, "source_window_id": split, "split": split,
                  "labels": "must-not-be-opened.json"}
        (root / "manifest.json").write_text(json.dumps({"selection_sha256": "frozen", "split": split,
                                                        "samples": [sample]}))
    report = audit_numeric_datasets(tmp_path, "frozen")
    assert {row["observation_id"] for row in report["rows"]} == {"train", "calibration", "test"}
    assert all(not row["eligible"] for row in report["rows"])
    original = (tmp_path / "train/manifest.json").read_bytes()
    copied = copy_eligible_dataset(tmp_path / "train", tmp_path / "fit", report, "audit-hash")
    assert (tmp_path / "train/manifest.json").read_bytes() == original
    assert json.loads((copied / "manifest.json").read_text())["samples"] == []


def test_test_nodata_remains_an_abstention_and_never_an_accuracy_success(tmp_path, monkeypatch):
    from puertogt.ml.public_attribute_eligibility import evaluate_with_numeric_abstentions

    original, eligible = tmp_path / "original", tmp_path / "eligible"
    original.mkdir()
    eligible.mkdir()
    features = [{"properties": {"id": "absent", "family": "tanquero", "length_m": 100, "width_m": 20,
        "provenance": {field: {"kind": "public_dataset", "reference": "https://example.test/pinned"}
                       for field in ("family", "length_m", "width_m")}}}]
    labels = original / "labels.json"
    labels.write_text(json.dumps({"features": features}))
    sample = {"observation_id": "absent", "labels": labels.name, "sha256": sha256_file(labels),
              "port_id": "public", "sensor": "sentinel-2", "zone_id": "context", "split_groups": ["test-pass"]}
    path = original / "manifest.json"
    path.write_text(json.dumps({"split": "test", "samples": [sample]}))
    (eligible / "manifest.json").write_text(json.dumps({"split": "test", "samples": []}))
    weights = tmp_path / "weights"
    weights.write_bytes(b"fixture")
    model = SimpleNamespace(weights_path=str(weights), weights_sha256=sha256_file(weights), model_id="fixture",
                            version="1", metadata={"training": {"split_groups": ["train-pass"]}})
    report = {"manifest_sha256": {"test": sha256_file(path)}, "summary": {"test": {"selected": 1,
        "eligible": 0, "ineligible": 1}}, "rows": [{"observation_id": "absent", "split": "test",
        "eligible": False, "reasons": ["reference_center_unobserved"]}]}
    def forbidden(*args, **kwargs):
        raise AssertionError("An ineligible reference must never invoke the model")
    monkeypatch.setattr("puertogt.ml.attribute_workflows.evaluate_attributes", forbidden)
    result = evaluate_with_numeric_abstentions(original, eligible, model, report)
    family = result["overall"]["family"]["raw"]
    assert family["reference_count"] == 1
    assert family["abstention_fraction"] == 1
    assert family["per_class"]["tanquero"]["recall"] == 0
    assert result["numeric_coverage"]["selected"] == 1
