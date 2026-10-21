import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from puertogt.contracts import ModelManifest, Prediction
from puertogt.ml.workflows import evaluation_inputs, labels_for_sample, train_detector
from puertogt.review import export_dataset, record_review
from puertogt.store import Store, sha256_file


def dataset(root, *, split="train", outside_label=False, reviewed=True):
    root.mkdir()
    labels = root / "labels.geojson"
    geometry = box(2.2, 2.2, 2.8, 2.8) if outside_label else box(0.2, 2.2, 0.8, 2.8)
    labels.write_text(json.dumps({"features": [{"geometry": mapping(geometry), "properties": {}}]}))
    sample = {
        "observation_id": "obs",
        "labels": labels.name,
        "sha256": sha256_file(labels),
        "port_id": "prq",
        "zone_id": "prq_muelles",
        "sensor": "sentinel-2",
    }
    if reviewed:
        sample["reviewed_area"] = mapping(box(0, 0, 2, 4))
    (root / "manifest.json").write_text(
        json.dumps({"version": "1", "split": split, "samples": [sample]})
    )
    return sample


def model():
    return ModelManifest(
        model_id="test",
        version="1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="ai2-s2-v1",
    )


def test_evaluation_excludes_predictions_outside_frozen_reviewed_area(tmp_path):
    root = tmp_path / "test"
    dataset(root, split="test")
    predictions = [
        Prediction(
            prediction_id=str(i), geometry=mapping(box(x, 2.2, x + 0.6, 2.8)), detection_score=0.9
        )
        for i, x in enumerate([0.2, 2.2])
    ]
    with (
        patch("puertogt.ml.detection.load_detector", return_value=object()),
        patch("puertogt.ml.detection.detect", return_value=predictions),
        patch("puertogt.ml.workflows.bundle_from_evidence", return_value=object()),
    ):
        _, records = evaluation_inputs(root, model(), required_split="test")
    assert [item["prediction_id"] for item in records[0]["predictions"]] == ["0"]


def test_training_preserves_context_and_masks_supervision_and_padding(tmp_path):
    root = tmp_path / "train"
    dataset(root)
    captured = []
    detector = SimpleNamespace(
        model=object(),
        make_optimizer=lambda **kw: object(),
        frozen_train_step=lambda image, boxes, **kw: captured.append((image.copy(), kw)) or {},
    )
    bundle = SimpleNamespace(crs="EPSG:4326", transform=tuple(from_origin(0, 4, 1, 1))[:6])
    with (
        patch("puertogt.ml.detection.load_detector", return_value=detector),
        patch("puertogt.ml.workflows.bundle_from_evidence", return_value=bundle),
        patch(
            "puertogt.preprocess.read_model_input",
            return_value=np.ones((9, 4, 4), dtype=np.float32),
        ),
        patch("puertogt.ml.training.export_training_artifact", return_value=model()),
    ):
        train_detector(root, model(), tmp_path / "out")
    image, kwargs = captured[0]
    assert np.all(image[:, :4, :4] == 1)
    mask = kwargs["reviewed_mask"]
    assert mask.dtype == bool and mask.shape == image.shape[-2:] == (32, 32)
    assert np.all(mask[:4, :2])
    assert not mask[:, 2:].any() and not mask[4:].any()


def test_training_without_any_supervised_step_does_not_export_weights(tmp_path):
    root = tmp_path / "train"
    dataset(root)
    detector = SimpleNamespace(
        model=object(), make_optimizer=lambda **kw: object(),
        frozen_train_step=lambda *args, **kw: {"skipped": "No supervised anchors or proposals"},
    )
    bundle = SimpleNamespace(crs="EPSG:4326", transform=tuple(from_origin(0, 4, 1, 1))[:6])
    with (
        patch("puertogt.ml.detection.load_detector", return_value=detector),
        patch("puertogt.ml.workflows.bundle_from_evidence", return_value=bundle),
        patch("puertogt.preprocess.read_model_input", return_value=np.ones((9, 4, 4), dtype=np.float32)),
        patch("puertogt.ml.training.export_training_artifact") as export,
    ):
        with pytest.raises(ValueError, match="training artifact was not created"):
            train_detector(root, model(), tmp_path / "out")
    export.assert_not_called()


def test_label_outside_reviewed_area_is_rejected(tmp_path):
    root = tmp_path / "train"
    sample = dataset(root, outside_label=True)
    with pytest.raises(ValueError, match="reviewed area"):
        labels_for_sample(root, sample)


def test_external_samples_without_reviewed_area_keep_full_crop_scope(tmp_path):
    root = tmp_path / "test"
    dataset(root, split="test", reviewed=False)
    prediction = Prediction(
        prediction_id="outside-zone", geometry=mapping(box(2.2, 2.2, 2.8, 2.8)), detection_score=0.9
    )
    with (
        patch("puertogt.ml.detection.load_detector", return_value=object()),
        patch("puertogt.ml.detection.detect", return_value=[prediction]),
        patch("puertogt.ml.workflows.bundle_from_evidence", return_value=object()),
    ):
        _, records = evaluation_inputs(root, model(), required_split="test")
    assert len(records[0]["predictions"]) == 1
    assert records[0]["reviewed_area_scope"] == "full_crop"


def reviewed_store(root):
    store = Store(root / "catalog.sqlite")
    store.register_scene(
        dict(
            scene_id="s",
            acquisition_key="take",
            sensor="sentinel-2",
            product_level="L1C",
            captured_at="2025-01-02T00:00:00Z",
            footprint={},
            port_ids=["prq"],
        )
    )
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,port_id,sensor,captured_at,status,detection_count) VALUES ('obs','take','prq_muelles','test','prq','sentinel-2','2025-01-02T00:00:00Z','usable',0)"
        )
    record_review(
        store,
        "obs",
        {"dataset_split": "train", "scene_complete": True},
        "human",
        entity_type="observation",
    )
    return store


def test_export_freezes_reviewed_area_and_configuration_hash(tmp_path):
    store = reviewed_store(tmp_path)
    result = export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    sample = result["samples"][0]
    assert sample["reviewed_area"]["type"] in {"Polygon", "MultiPolygon"}
    assert sample["reviewed_area_source"]["zone_id"] == "prq_muelles"
    assert len(sample["reviewed_area_source"]["ports_config_sha256"]) == 64


def test_failed_dataset_export_leaves_destination_reusable(tmp_path):
    store = reviewed_store(tmp_path)
    source = tmp_path / "evidence.tif"
    source.write_bytes(b"original")
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO assets(asset_id,observation_id,path,sha256,size_bytes,mime_type,created_at) VALUES ('a','obs','evidence.tif',?,8,'image/tiff','2026-10-21T00:00:00Z')",
            (sha256_file(source),),
        )
    source.write_bytes(b"changed!")
    destination = tmp_path / "dataset"
    with pytest.raises(ValueError, match="checksum"):
        export_dataset(store, destination, split="train", version="v1")
    assert not destination.exists()
    source.write_bytes(b"original")
    export_dataset(store, destination, split="train", version="v1")
    assert (destination / "manifest.json").is_file()
