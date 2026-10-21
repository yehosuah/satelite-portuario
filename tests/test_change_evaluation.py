import json

import pytest
from test_change_workflows import change_manifest, make_pair_dataset

from puertogt.ml.change import TemporalEvidence, track_change
from puertogt.store import json_text


def frozen_pairs(tmp_path):
    root = tmp_path / "test-pairs"
    manifest = make_pair_dataset(root)
    manifest.update(split="test", frozen=True)
    for index, sample in enumerate(manifest["samples"]):
        sample.update(
            port_id="prq" if index == 0 else "stc",
            zone_id="muelles" if index == 0 else "patios",
            sensor="sentinel-2",
        )
        sample["labels"]["review_kind"] = "human"
    (root / "manifest.json").write_text(json_text(manifest))
    model = change_manifest(tmp_path)
    model.metadata["training"] = {
        "split_groups": ["training-series"],
        "acquisition_ids": ["train-t0", "train-t1"],
        "observation_ids": ["train-o0", "train-o1"],
        "dataset_manifest_sha256": "a" * 64,
    }
    return root, manifest, model


def candidates(reference, target, model):
    from datetime import datetime

    from pyproj import Transformer
    from shapely.geometry import box, mapping
    from shapely.ops import transform

    polygon = (
        box(300020, 1539940, 300060, 1539980)
        if target.observation_id == "target-0"
        else box(300000, 1539980, 300020, 1540000)
    )
    geometry = mapping(
        transform(Transformer.from_crs(target.crs, "EPSG:4326", always_xy=True).transform, polygon)
    )
    left = TemporalEvidence(
        reference.observation_id, datetime.fromisoformat(reference.metadata["captured_at"]), False
    )
    right = TemporalEvidence(
        target.observation_id, datetime.fromisoformat(target.metadata["captured_at"]), True
    )
    return [track_change(left, [right], geometry=geometry, model_id=model.model_id)]


def test_change_evaluation_scores_frozen_pairs_and_keeps_failed_stratum_visible(
    tmp_path, monkeypatch
):
    from puertogt.ml import change_evaluation

    root, _, model = frozen_pairs(tmp_path)
    original = model.model_dump(mode="json")
    monkeypatch.setattr(change_evaluation, "compare", candidates)
    report = change_evaluation.evaluate_change_dataset(root, model)
    assert report["overall"]["true_positive_pixels"] == 16
    assert report["overall"]["false_positive_pixels"] == 4
    assert report["overall"]["iou"] == pytest.approx(0.8)
    assert report["overall"]["precision"] == pytest.approx(0.8)
    assert report["overall"]["recall"] == 1
    assert report["overall"]["false_positive_area_m2"] == pytest.approx(400)
    assert report["strata"]["stc|sentinel-2|patios"]["negative_pair_false_alarm_fraction"] == 1
    assert report["threshold"] == 0.5
    assert report["automatic_validation"] is False
    assert len(report["pairs"]) == 2
    assert model.model_dump(mode="json") == original


def test_change_evaluation_counts_omissions_when_model_returns_no_candidates(tmp_path, monkeypatch):
    from puertogt.ml import change_evaluation

    root, _, model = frozen_pairs(tmp_path)
    monkeypatch.setattr(change_evaluation, "compare", lambda *args: [])
    report = change_evaluation.evaluate_change_dataset(root, model)
    assert report["overall"]["false_negative_pixels"] == 16
    assert report["overall"]["false_negative_area_m2"] == pytest.approx(1600)
    assert report["overall"]["recall"] == 0
    assert report["overall"]["precision"] is None
    assert report["overall"]["negative_pair_false_alarm_fraction"] == 0


@pytest.mark.parametrize("identity", ["split_groups", "acquisition_ids", "observation_ids"])
def test_change_evaluation_rejects_each_training_overlap_before_inference(
    tmp_path, monkeypatch, identity
):
    from puertogt.ml import change_evaluation

    root, manifest, model = frozen_pairs(tmp_path)
    sample = manifest["samples"][0]
    value = {
        "split_groups": sample["split_groups"][0],
        "acquisition_ids": sample["reference"]["acquisition_key"],
        "observation_ids": sample["target"]["observation_id"],
    }[identity]
    model.metadata["training"][identity] = [value]
    monkeypatch.setattr(
        change_evaluation, "compare", lambda *args: pytest.fail("must reject before inference")
    )
    with pytest.raises(ValueError, match="leakage"):
        change_evaluation.evaluate_change_dataset(root, model)


@pytest.mark.parametrize("invalid", ["port_id", "zone_id", "human", "frozen", "checksum"])
def test_change_evaluation_rejects_unattributed_or_unfrozen_test_data(
    tmp_path, monkeypatch, invalid
):
    from puertogt.ml import change_evaluation

    root, manifest, model = frozen_pairs(tmp_path)
    if invalid in {"port_id", "zone_id"}:
        manifest["samples"][0].pop(invalid)
    elif invalid == "human":
        manifest["samples"][0]["labels"]["review_kind"] = "model"
    elif invalid == "frozen":
        manifest["frozen"] = False
    else:
        (root / "labels-0.tif").write_bytes(b"altered")
    (root / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        change_evaluation, "compare", lambda *args: pytest.fail("must reject before inference")
    )
    with pytest.raises(ValueError):
        change_evaluation.evaluate_change_dataset(root, model)
