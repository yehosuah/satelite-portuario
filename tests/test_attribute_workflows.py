import json
import math

import numpy as np
import pytest

from puertogt.contracts import ModelManifest, Prediction, RasterBundle
from puertogt.ml.attribute_workflows import evaluate_attributes
from puertogt.ml.attributes import build_attribute_head, estimate_attributes, predict_attributes
from puertogt.store import sha256_file


@pytest.fixture
def attribute_fixture(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    network = build_attribute_head(2, hidden_dim=4)
    with torch.no_grad():
        for parameter in network.parameters():
            parameter.zero_()
        network.family.bias[0] = 5
        network.dimensions.bias[:] = torch.tensor([math.log(math.expm1(1.2)), math.log(math.expm1(1.5))])
    weights = tmp_path / "weights.pt"
    torch.save({"state_dict": network.state_dict()}, weights)
    encoder = ModelManifest(model_id="encoder", version="v1", sensor="sentinel-2", task="detection",
                            recipe_version="ai2-s2-v1", weights_sha256="b" * 64)
    model = ModelManifest(model_id="attributes", version="v1", sensor="sentinel-2", task="attributes",
                          recipe_version="ai2-s2-v1", weights_path=str(weights), weights_sha256=sha256_file(weights),
                          publication_policy={"automatic_publication": False, "fields": {}}, metadata={
                              "architecture": "pooled_fpn_mlp_v1", "feature_dim": 2, "hidden_dim": 4,
                              "encoder_manifest": encoder.model_dump(mode="json"),
                              "training": {"observation_ids": ["train-obs"], "split_groups": ["train-take"],
                                           "dataset_manifest_sha256": "a" * 64,
                                           "dimension_baseline_m": {"length_m": 100, "width_m": 20}},
                          })
    bundle = RasterBundle(band_paths={}, crs="EPSG:32615", transform=(10, 0, 0, 0, -10, 0), units={},
                          preprocessing_version="ai2-s2-v1", sensor="sentinel-2", product_level="L1C")
    monkeypatch.setattr("puertogt.ml.attributes.extract_features", lambda bundle, detections, encoder, **kwargs: np.ones((len(detections), 2), dtype=np.float32))
    monkeypatch.setattr("puertogt.ml.attribute_workflows.bundle_from_evidence", lambda sample, root: bundle)
    dataset = tmp_path / "test"
    dataset.mkdir()
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    properties = {"id": "reference-one", "family": "carga", "length_m": 120, "width_m": 30,
                  "provenance": {"family": {"kind": "independent_reference", "reference": "documented-test"},
                                 "length_m": {"kind": "independent_reference", "reference": "documented-test"}}}
    labels = dataset / "labels.geojson"
    labels.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": geometry, "properties": properties}]}))
    manifest = {"version": "held-out-v1", "split": "test", "samples": [{"observation_id": "test-obs", "split_groups": ["test-take"],
        "port_id": "quetzal", "sensor": "sentinel-2", "zone_id": "dock", "labels": labels.name, "sha256": sha256_file(labels)}]}
    (dataset / "manifest.json").write_text(json.dumps(manifest))
    return dataset, model, bundle, geometry


def test_raw_estimates_are_available_before_publication_is_validated(attribute_fixture):
    _, model, bundle, geometry = attribute_fixture
    detection = Prediction(prediction_id="one", geometry=geometry, detection_score=0.9)
    raw = estimate_attributes(bundle, [detection], model)[0]
    assert raw["family"] == "carga"
    assert raw["length_m"] == pytest.approx(120, abs=0.0001)
    assert sum(raw["family_probabilities"].values()) == pytest.approx(1)
    published = predict_attributes(bundle, [detection], model)[0]
    assert published.family is None and published.length_m is None
    assert published.detection_score == 0.9


def test_evaluation_uses_reference_boxes_and_independent_field_provenance(attribute_fixture):
    dataset, model, _, _ = attribute_fixture
    report = evaluate_attributes(dataset, model, family_threshold=0.99)
    assert report["evaluation_scope"] == "attribute_head_on_reference_objects"
    assert report["overall"]["family"]["raw"]["per_class"]["carga"]["recall"] == 1
    assert report["overall"]["family"]["thresholded"]["abstention_fraction"] == 1
    dimensions = report["strata"]["quetzal|sentinel-2|dock"]["dimensions"]
    assert dimensions["length_m"]["mae_m"] == pytest.approx(0, abs=0.0001)
    assert dimensions["length_m"]["baseline_mae_m"] == 20
    assert dimensions["width_m"]["reference_count"] == 0
    assert report["publication_decision"]["automatic_publication"] is False
    assert model.publication_policy["fields"] == {}


def test_evaluation_rejects_training_overlap_and_calibration_split(attribute_fixture):
    dataset, model, _, _ = attribute_fixture
    path = dataset / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["samples"][0]["split_groups"] = ["train-take"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="related"):
        evaluate_attributes(dataset, model)
    manifest["split"] = "calibration"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="test"):
        evaluate_attributes(dataset, model)
