import pytest

from puertogt.ml.evaluation import (
    calibrate_threshold,
    classification_report,
    detection_report,
    dimension_report,
    match_detections,
    precision_at_counts,
    publication_decision,
)


def test_precision_counts_false_reports():
    assert precision_at_counts(true_positives=90, false_positives=10) == 0.90
    assert precision_at_counts(true_positives=0, false_positives=0) is None


def test_duplicate_boxes_count_as_false_positive_and_missing_box_as_omission():
    def box(x):
        return {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}
    counts = match_detections([
        {"geometry": box(0), "detection_score": 0.99}, {"geometry": box(0), "detection_score": 0.9},
    ], [{"geometry": box(0)}, {"geometry": box(3)}])
    assert counts == {"true_positives": 1, "false_positives": 1, "false_negatives": 1}


def test_bad_or_absent_stratum_cannot_hide_behind_high_average():
    report = detection_report([
        {"port_id": "quetzal", "sensor": "sentinel-2", "zone_id": "open", "true_positives": 990,
         "false_positives": 10, "false_negatives": 50},
        {"port_id": "barrios", "sensor": "sentinel-2", "zone_id": "dock", "true_positives": 80,
         "false_positives": 20, "false_negatives": 10},
    ], dataset_hash="a" * 64, independent_reference=True, test_frozen=True)
    assert report["overall"]["precision"] > 0.9
    policy = publication_decision(report, required_strata=["quetzal|sentinel-2|open", "barrios|sentinel-2|dock"],
                                  min_support=50)
    assert policy["status"] == "experimental"
    assert policy["automatic_publication"] is False
    assert policy["strata"]["barrios|sentinel-2|dock"]["eligible"] is False
    missing = publication_decision(report, required_strata=["santo-tomas|sentinel-1|dock"], min_support=50)
    assert "missing_stratum" in missing["reasons"]


def test_frozen_independent_test_is_required_even_with_perfect_counts():
    record = {"port_id": "p", "sensor": "sentinel-1", "zone_id": "z", "true_positives": 100,
              "false_positives": 0, "false_negatives": 10}
    unsafe = detection_report([record])
    assert not publication_decision(unsafe, required_strata=["p|sentinel-1|z"], min_support=50)["automatic_publication"]
    safe = detection_report([record], dataset_hash="b" * 64, independent_reference=True, test_frozen=True)
    decision = publication_decision(safe, required_strata=["p|sentinel-1|z"], min_support=50)
    assert decision["status"] == "validated"
    assert safe["overall"]["precision_interval"][0] < 1
    assert safe["overall"]["recall"] == pytest.approx(100 / 110)


def test_threshold_cannot_be_selected_using_test_set():
    records = [{"score": 0.9, "matched": True}, {"score": 0.2, "matched": False}]
    with pytest.raises(ValueError, match="calibration"):
        calibrate_threshold(records, split="test", target_precision=0.9, min_predictions=1)
    calibrated = calibrate_threshold(records, split="calibration", target_precision=0.9, min_predictions=1)
    assert calibrated["threshold"] == 0.9


def test_attribute_abstentions_count_against_recall_and_dimensions_keep_nulls():
    result = classification_report(["carga", "carga", "tanquero"], ["carga", None, "carga"])
    assert result["abstention_fraction"] == pytest.approx(1 / 3)
    assert result["per_class"]["carga"]["recall"] == 0.5
    dimensions = dimension_report([100, 200, 300], [110, None, 270], baseline=180)
    assert dimensions["reference_count"] == 3
    assert dimensions["estimated_count"] == 2
    assert dimensions["mae_m"] == 20
    assert dimensions["bias_m"] == -10
    assert dimensions["baseline_mae_m"] == 100


def test_attributes_keep_detection_and_abstain_per_unvalidated_field():
    from puertogt.contracts import Prediction
    from puertogt.ml.attributes import apply_attribute_policy
    detection = Prediction(prediction_id="one", geometry={"type": "Point", "coordinates": [0, 0]}, detection_score=0.95)
    result = apply_attribute_policy(detection, {"family": "carga", "family_probability": 0.99,
                                               "length_m": 105, "width_m": 17},
                                    {"family": {"validated": True, "reference_support": 100, "min_support": 50,
                                                "allowed_families": ["carga"], "min_probability": 0.9},
                                     "length_m": {"validated": False}, "width_m": {"validated": False}})
    assert result.prediction_id == "one"
    assert result.detection_score == 0.95
    assert result.family == "carga"
    assert result.length_m is None and result.width_m is None
    assert result.field_quality["length_m"]["reason"] == "field_not_validated"


def test_masked_loss_ignores_missing_labels_including_nan_dimensions():
    torch = pytest.importorskip("torch")
    from puertogt.ml.attributes import masked_attribute_loss
    logits = torch.tensor([[2., 0.], [0., 2.]], requires_grad=True)
    dimensions = torch.tensor([[1., 2.], [30., 40.]], requires_grad=True)
    loss = masked_attribute_loss(logits, dimensions, torch.tensor([0, -1]),
                                 torch.tensor([[1., 2.], [float("nan"), float("nan")]]),
                                 torch.tensor([True, False]), torch.tensor([[True, True], [False, False]]))
    loss.backward()
    assert torch.isfinite(loss)
    assert logits.grad[1].abs().sum() == 0
    assert dimensions.grad[1].abs().sum() == 0


def test_training_refuses_test_split_before_reading_tensors(tmp_path):
    import json

    from puertogt.ml.training import validate_training_dataset
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "held-out", "split": "test", "samples": []}))
    with pytest.raises(ValueError, match="train"):
        validate_training_dataset(tmp_path)


def test_train_real_head_exports_changed_weights_with_dataset_hash(tmp_path):
    torch = pytest.importorskip("torch")
    import json

    import numpy as np

    from puertogt.contracts import ModelManifest
    from puertogt.ml.training import train_attribute_dataset
    from puertogt.store import sha256_file
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    labels = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {
        "id": "one", "family": "carga", "length_m": 100, "width_m": None,
        "provenance": {"family": {"kind": "independent_reference", "reference": "fixture"},
                       "length_m": {"kind": "independent_reference", "reference": "fixture"}},
    }}]}
    (dataset / "labels.geojson").write_text(json.dumps(labels))
    np.savez(dataset / "features.npz", features=np.ones((1, 4), dtype=np.float32), ids=np.array(["one"]))
    manifest = {"version": "fixture-v1", "split": "train", "samples": [{"observation_id": "obs", "split_groups": ["take"],
        "labels": "labels.geojson", "sha256": sha256_file(dataset / "labels.geojson"),
        "feature_cache": {"path": "features.npz", "sha256": sha256_file(dataset / "features.npz"),
                          "encoder_sha256": "a" * 64, "pooling": "roi_mean", "feature_level": 0}}]}
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    base = ModelManifest(model_id="own-attrs", version="v1", sensor="sentinel-2", task="attributes", recipe_version="ai2-s2-v1",
                         metadata={"feature_dim": 4, "encoder_manifest": {"weights_sha256": "a" * 64}})
    result = train_attribute_dataset(dataset, base, tmp_path / "trained", epochs=2, seed=7)
    assert result.weights_sha256 == sha256_file(tmp_path / "trained/weights.pt")
    assert result.dataset_version == "fixture-v1"
    assert result.publication_policy["automatic_publication"] is False
    assert result.metadata["training"]["dataset_manifest_sha256"] == sha256_file(dataset / "manifest.json")
    assert result.metadata["training"]["updated_parameters"] is True
    assert result.metadata["training"]["label_support"]["width_m"] == 0
    assert result.metadata["training"]["dimension_baseline_m"] == {"length_m": 100.0, "width_m": None}
    assert result.metadata["training"]["observation_ids"] == ["obs"]
    assert result.metadata["training"]["split_groups"] == ["take"]
    state = torch.load(tmp_path / "trained/weights.pt", weights_only=True)["state_dict"]
    assert state
