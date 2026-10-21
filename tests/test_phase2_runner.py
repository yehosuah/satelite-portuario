import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from test_phase2_workflows import frozen_history, model

from puertogt.store import sha256_file

SPEC = importlib.util.spec_from_file_location(
    "train_phase2", Path(__file__).parents[1] / "scripts/train_phase2.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_evaluation_cannot_open_test_before_training_and_calibration(tmp_path):
    with pytest.raises(ValueError, match="train.*receipt"):
        runner.require_stage_receipts(tmp_path, "evaluate")


def receipt(root, stage, *, passed=True):
    root.mkdir(exist_ok=True)
    (root / "freeze.json").write_text("{}")
    artifact = root / f"{stage}-artifact.json"
    artifact.write_text("{}")
    (root / f"{stage}.completed.json").write_text(json.dumps({
        "freeze_sha256": sha256_file(root / "freeze.json"),
        "outputs": {artifact.name: sha256_file(artifact)},
        "status": "passed" if passed else "failed"}))


def test_failed_calibration_blocks_test_before_loading_dataset(tmp_path, monkeypatch):
    receipt(tmp_path, "train")
    receipt(tmp_path, "calibrate", passed=False)
    monkeypatch.setattr(runner, "load_freeze", lambda *a: pytest.fail("Test dataset was opened"))
    with pytest.raises(ValueError, match="Unsuccessful calibrate"):
        runner.run_evaluation(tmp_path)


def test_receipt_detects_changed_completed_artifact(tmp_path):
    receipt(tmp_path, "train")
    (tmp_path / "train-artifact.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="output checksum"):
        runner.require_stage_receipts(tmp_path, "calibrate")


def test_same_pass_cannot_be_retested_in_new_experiment_or_manifest(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    for root in [first, second]:
        root.mkdir()
        (root / "freeze.json").write_text("{}")
    freeze = {"test_registry": str(tmp_path / "registry"), "datasets": {"test": {"sha256": "a" * 64}}}
    data = {"samples": [{"sensor": "sentinel-1", "pass_id": "S1A:057250:070AE5"}] * 2}
    runner.reserve_test_once(freeze, data, experiment=first)
    freeze["datasets"]["test"]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="already consumed"):
        runner.reserve_test_once(freeze, data, experiment=second)
    assert len(list((tmp_path / "registry").glob("*.json"))) == 1


def test_started_stage_cannot_be_silently_repeated(tmp_path):
    (tmp_path / "freeze.json").write_text("{}")
    runner.start_stage(tmp_path, "train")
    with pytest.raises(FileExistsError):
        runner.start_stage(tmp_path, "train")


def test_preflight_hashes_test_labels_without_parsing_their_geometries(tmp_path):
    root = tmp_path / "test"
    data = frozen_history(root)
    (root / "labels.geojson").write_bytes(b"Only checksum verification is allowed in preflight.")
    for sample in data["samples"]:
        sample["sha256"] = sha256_file(root / "labels.geojson")
    (root / "manifest.json").write_text(json.dumps(data))
    _, metadata = runner.inspect_split(root, sha256_file(root / "manifest.json"), "test", model())
    assert metadata["ports"] == {"prq"}
    (root / "labels.geojson").write_bytes(b"changed")
    with pytest.raises(ValueError, match="label checksum"):
        runner.inspect_split(root, sha256_file(root / "manifest.json"), "test", model())


def test_derived_train_requires_matching_parent_source(tmp_path):
    root = tmp_path / "train"
    data = frozen_history(root, split="train")
    data["parent_manifest_sha256"] = "a" * 64
    (root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="train-source"):
        runner.inspect_split(root, sha256_file(root / "manifest.json"), "train", model())
    with pytest.raises(ValueError, match="parent checksum"):
        runner.inspect_split(root, sha256_file(root / "manifest.json"), "train", model(), train_source=root)


@pytest.mark.parametrize("nodata,value,has_signal", [
    (0, 0, False), (None, 0, True), (0, 1, True), (float("nan"), float("nan"), False),
])
def test_only_explicit_all_nodata_rasters_are_exempt_from_leakage(tmp_path, nodata, value, has_signal):
    path = tmp_path / "numeric.tif"
    with rasterio.open(path, "w", driver="GTiff", height=16, width=16, count=2,
                       dtype="float32", nodata=nodata, transform=rasterio.Affine(10, 0, 500000, 0, -10, 1500000)) as dst:
        dst.write(np.full((2, 16, 16), value, dtype="float32"))
    assert runner.raster_has_signal(path) is has_signal
    if not has_signal:
        with rasterio.open(path, "r+") as dst:
            data = dst.read(2)
            data[15, 15] = 7
            dst.write(data, 2)
        assert runner.raster_has_signal(path) is True


def test_nodata_exemption_keeps_integrity_and_nonempty_hash_checks(tmp_path):
    root = tmp_path / "test"
    data = frozen_history(root)
    path = root / "tile-0-bands.tif"
    with rasterio.open(path, "r+") as dst:
        dst.nodata = 0
        dst.write(np.zeros((dst.count, dst.height, dst.width), dtype="uint16"))
    digest = sha256_file(path)
    data["history_inference"]["acquisitions"][0]["tiles"][0]["file_sha256"][path.name] = digest
    (root / "manifest.json").write_text(json.dumps(data))
    _, metadata = runner.inspect_split(root, sha256_file(root / "manifest.json"), "test", model())
    assert digest not in metadata["numeric_hashes"]
    assert digest in metadata["all_nodata_hashes"]
    assert sha256_file(root / "tile-1-bands.tif") in metadata["numeric_hashes"]
    with rasterio.open(path, "r+") as dst:
        values = dst.read(1)
        values[0, 0] = 42
        dst.write(values, 1)
    with pytest.raises(ValueError, match="checksum"):
        runner.inspect_split(root, sha256_file(root / "manifest.json"), "test", model())


def test_reserve_activation_is_specific_to_its_split_port_sensor_and_primary():
    selection = {"slots": [
        {"split": "train", "port_id": port, "sensor": "sentinel-2",
         "selected": {"acquisition_key": "primary"}, "reserves": [{"acquisition_key": "reserve"}]}
        for port in ["pbr", "stc"]]}
    activation = {"split": "train", "port_ids": ["pbr"], "sensor": "sentinel-2",
                  "failed_primary_acquisition_key": "primary", "reserve_acquisition_key": "reserve",
                  "reason": "No observable area", "used_predictions_or_scores": False}
    allowed = runner.allowed_assignments(selection, {"activated_reserves": [activation]})
    assert ("train", "pbr", "sentinel-2", "reserve") in allowed
    assert ("train", "stc", "sentinel-2", "reserve") not in allowed
    assert ("train", "stc", "sentinel-2", "primary") in allowed
    for override in [{"split": "test"}, {"failed_primary_acquisition_key": "different"},
                     {"used_predictions_or_scores": True}]:
        with pytest.raises(ValueError, match="activation"):
            runner.allowed_assignments(selection, {"activated_reserves": [{**activation, **override}]})
