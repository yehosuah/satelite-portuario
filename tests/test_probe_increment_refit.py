import importlib.util
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from puertogt.contracts import ModelManifest
from puertogt.store import sha256_file

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("probe_increment_refit", SCRIPTS / "probe_increment_refit.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def dataset(day="20250304", *, split="train"):
    key = f"S2A:MSIL1C:{day}T162211:R040:T15PYR"
    pass_id = f"S2A:{day}T162211:R040"
    return {"split": split, "samples": [{
        "sensor": "sentinel-2", "observation_id": f"obs-{day}", "acquisition_key": key,
        "pass_id": pass_id, "split_groups": [key, pass_id],
    }]}


@pytest.mark.parametrize("scope", ["calibration", "test", "mixed"])
def test_reserved_or_mixed_inputs_fail_at_metadata_boundary(scope):
    value = dataset(split=scope if scope != "mixed" else "train")
    if scope == "mixed":
        value["samples"][0]["split"] = "test"
    with pytest.raises(ValueError, match="TRAIN"):
        runner.train_identity(value)


@pytest.mark.parametrize("collision", ["physical_pass", "related_group", "parent_id", "model_group"])
def test_refit_training_collisions_are_rejected_before_labels(collision):
    old, new = dataset("20250103"), dataset()
    prior, sample = old["samples"][0], new["samples"][0]
    model = SimpleNamespace(metadata={"training": {"split_groups": [], "observation_ids": []}})
    if collision == "physical_pass":
        sample["acquisition_key"] = prior["acquisition_key"].replace("T15PYR", "T16PCC")
        sample["pass_id"] = prior["pass_id"]
        sample["split_groups"] = [sample["acquisition_key"]]
    elif collision == "related_group":
        sample["split_groups"].append(prior["pass_id"])
    elif collision == "parent_id":
        sample["parent_observation_id"] = prior["observation_id"]
    else:
        model.metadata["training"]["split_groups"] = [sample["pass_id"]]
    with pytest.raises(ValueError, match="overlap"):
        runner.validate_unseen_passes(new, old, model)


@pytest.mark.parametrize("changed", ["labels.geojson", "review.tif", "halo.tif"])
def test_changed_label_review_asset_or_original_halo_fails_pins(tmp_path, changed):
    value = dataset()
    hashes = {}
    for name in ("labels.geojson", "review.tif", "halo.tif"):
        (tmp_path / name).write_bytes(name.encode())
        hashes[name] = sha256_file(tmp_path / name)
    value["samples"][0].update(labels="labels.geojson", sha256=hashes["labels.geojson"],
        evidence_assets=[{"path": "review.tif", "sha256": hashes["review.tif"]}])
    value["history_inference"] = {"acquisitions": [{
        "tiles": [{"file_sha256": {"halo.tif": hashes["halo.tif"]}}],
    }]}
    (tmp_path / "manifest.json").write_text(json.dumps(value))
    assert runner.dataset_pins(tmp_path, value)["halo.tif"] == hashes["halo.tif"]
    (tmp_path / changed).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        runner.dataset_pins(tmp_path, value)


def frozen_fixture(tmp_path, monkeypatch):
    model = ModelManifest(model_id="fixture", version="1", sensor="sentinel-2",
                          task="detection", recipe_version="ai2-s2-v1")
    inputs = {"dataset_sha256": "fixture-dataset", "new_pass_ids": ["new-pass"],
              "dataset_files": {"labels.geojson": "original-label-hash"}}
    monkeypatch.setattr(runner, "inspect_inputs", lambda digest: (model, inputs))
    monkeypatch.setattr(runner, "source_state", lambda: {"files": {"runner.py": "original-source"}})
    output = tmp_path / "probe"
    frozen = runner.freeze("fixture-dataset", output)
    return output, frozen["freeze_sha256"], inputs


@pytest.mark.parametrize("changed", ["source", "input_pins", "plan"])
def test_freeze_rejects_drift_before_inference(tmp_path, monkeypatch, changed):
    output, digest, inputs = frozen_fixture(tmp_path, monkeypatch)
    if changed == "source":
        monkeypatch.setattr(runner, "source_state", lambda: {"files": {"runner.py": "changed"}})
    elif changed == "input_pins":
        inputs["dataset_files"]["labels.geojson"] = "changed"
    else:
        plan = json.loads((output / "plan.json").read_text())
        plan["settings"]["score_threshold"] = .8
        (output / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="Frozen"):
        runner.verify_frozen(output, digest)


def test_probe_uses_one_fixed_threshold_and_never_claims_validation(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    output, digest, _ = frozen_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "probe_budget", nullcontext)
    sample = {**dataset()["samples"][0], "port_id": "prq", "zone_id": "prq_fondeo"}
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    records = [{"sample": sample, "inference_purpose": "training_fit_only",
                "predictions": [{"prediction_id": "p", "geometry": geometry, "detection_score": .9}],
                "references": [{"geometry": geometry, "properties": {
                    "provenance": {"unique_physical_object_key": "reference"}}}]}]
    calls = []

    def diagnostic(path, model, **kwargs):
        calls.append(model.publication_policy["score_threshold"])
        assert kwargs == {"cache_dir": output / "cache", "device": "cpu"}
        return dataset(), records

    monkeypatch.setattr(runner, "history_training_diagnostic_inputs", diagnostic)
    result = runner.run(output, digest)
    report = json.loads((output / "report.json").read_text())
    assert calls == [.5]
    assert report["threshold"] == report["iou_threshold"] == .5
    assert report["overall"]["true_positives"] == 1
    assert report["eligible"] is False
    assert report["unseen_by_refit_training"] is True
    assert report["independent_validation"] is False
    assert result["training_performed"] is result["model_activation_performed"] is False
    assert result["threshold_selection_performed"] is result["automatic_publication"] is False
    assert json.loads((output / "predictions.json").read_text())[0]["inference_purpose"] == "training_fit_only"
    with pytest.raises(FileExistsError, match="cannot be repeated"):
        runner.run(output, digest)


def test_failed_probe_is_recorded_and_cannot_be_retried(tmp_path, monkeypatch):
    output, digest, _ = frozen_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "probe_budget", nullcontext)

    def stale(*args):
        raise ValueError("Frozen source changed")

    monkeypatch.setattr(runner, "verify_frozen", stale)
    with pytest.raises(ValueError, match="Frozen source"):
        runner.run(output, digest)
    assert json.loads((output / "failed.json").read_text())["immutable_attempt"] is True
    assert not (output / "completed.json").exists()
    with pytest.raises(FileExistsError, match="cannot be repeated"):
        runner.run(output, digest)
