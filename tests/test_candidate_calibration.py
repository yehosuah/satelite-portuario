import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("candidate_calibration", SCRIPTS / "calibrate_phase2_candidate.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize("change", [
    {"calibration_and_test_used": True},
    {"final_refit": {"weights_sha256": "different"}},
    {"plan_sha256": "another-plan"},
    {"status": "failed"},
])
def test_rejects_contaminated_or_unrelated_candidate(change):
    model = SimpleNamespace(weights_sha256="weights", task="detection", publication_policy={})
    receipt = {"status": "completed_internal_development_only", "calibration_and_test_used": False,
               "final_refit": {"weights_sha256": "weights"}, "plan_sha256": "plan"}
    runner.validate_lineage(model, receipt, {"sha256": "plan"})
    with pytest.raises(ValueError, match="TRAIN-only"):
        runner.validate_lineage(model, {**receipt, **change}, {"sha256": "plan"})


def test_frozen_candidate_refuses_changed_bytes(tmp_path):
    path = tmp_path / "weights.pt"
    path.write_bytes(b"original")
    pins = {"weights": runner.pinned(path)}
    runner.verify_pins(pins)
    path.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="weights"):
        runner.verify_pins(pins)


def test_failed_inference_cannot_rerun_or_open_test(tmp_path, monkeypatch):
    (tmp_path / "freeze.json").write_text(json.dumps({"frozen": True}))
    calibration = tmp_path / "calibration-input"
    model = SimpleNamespace(publication_policy={})
    model.model_copy = lambda **kwargs: model
    value = {"datasets": {"calibration": {"path": str(calibration)}}}
    monkeypatch.setattr(runner, "load_frozen", lambda _: (value, model))
    monkeypatch.setattr(runner, "set_runtime", lambda: None)
    calls = []

    def fail(path, candidate, **kwargs):
        calls.append((path, kwargs["required_split"]))
        raise RuntimeError("interrupted numerical inference")

    monkeypatch.setattr(runner, "history_evaluation_inputs", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        runner.run(tmp_path)
    assert (tmp_path / "calibrate.started.json").exists()
    assert not (tmp_path / "calibrate.completed.json").exists()
    with pytest.raises(FileExistsError):
        runner.run(tmp_path)
    assert calls == [(calibration, "calibration")]
