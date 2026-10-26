import importlib.util
from pathlib import Path

import pytest
from shapely.geometry import box, mapping


@pytest.fixture
def diagnostic():
    path = Path(__file__).resolve().parents[1] / "scripts/diagnose_phase2_localization.py"
    spec = importlib.util.spec_from_file_location("phase2_localization", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(zone="prq_muelles"):
    truth = box(-90.8, 13.9, -90.799, 13.901)
    # An oversized box overlaps a real reference but does not pass IoU 0.5.
    predicted = box(-90.8005, 13.8995, -90.7985, 13.9015)
    return {"sample": {"port_id": "prq", "zone_id": zone, "sensor": "sentinel-2",
                       "acquisition_key": "acquisition", "pass_id": "pass"},
            "predictions": [{"prediction_id": "p", "geometry": mapping(predicted),
                             "detection_score": .9}],
            "references": [{"geometry": mapping(truth), "properties": {
                "provenance": {"unique_physical_object_key": "boat"}}}]}


def test_geometric_near_miss_stays_failed_and_overlapping_zones_do_not_inflate(diagnostic):
    result = diagnostic.diagnose([record(), record("prq_canal")], split="calibration")
    counts = result["counts"]["overall"]
    assert (counts["true_positives"], counts["false_positives"], counts["false_negatives"]) == (0, 1, 1)
    assert counts["false_positive_with_reference_overlap"] == 1
    assert counts["false_negative_with_prediction_overlap"] == 1
    assert result["local_validation_demonstrated"] is False
    assert result["geometry_distributions"]["prq"]["overlapping_fp_area_ratio"]["median"] == pytest.approx(4)


def test_test_split_refused_and_conflicting_duplicate_geometry_rejected(diagnostic):
    with pytest.raises(ValueError, match="TEST stays closed"):
        diagnostic.diagnose([record()], split="test")
    changed = record("prq_canal")
    changed["predictions"][0]["geometry"] = mapping(box(-90.8, 13.9, -90.799, 13.901))
    with pytest.raises(ValueError, match="Inconsistent duplicate"):
        diagnostic.diagnose([record(), changed], split="calibration")
