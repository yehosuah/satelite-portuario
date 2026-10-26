import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("refit_anchor_selection", SCRIPTS / "refit_anchor_selection.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
select_recipe = importlib.import_module("train_phase2_amendment").select_recipe


def completed(tp=17, fp=3, fn=3):
    candidates = []
    for name, sizes, counts in [
        ("baseline_anchors", [[32], [64], [128], [256]], (16, 4, 4)),
        ("small_anchors", [[8], [16], [32], [64]], (tp, fp, fn)),
    ]:
        candidates.append({"name": name, "anchor_sizes": sizes, "epochs": 12,
            "learning_rate": .0001, "sample_order": "shuffle", "overall": dict(zip(
                ("true_positives", "false_positives", "false_negatives"), counts))})
    return {"status": "completed_internal_train_development_only", "calibration_or_test_opened": False,
            "refit": None, "decision": select_recipe(candidates)}


def test_refit_requires_the_original_gain_and_recall_gate():
    assert runner.eligible_selection(completed(), select_recipe)["name"] == "small_anchors"
    for unsupported in (completed(16, 4, 4), completed(15, 0, 5)):
        with pytest.raises(ValueError, match="gate"):
            runner.eligible_selection(unsupported, select_recipe)


def test_refit_rejects_a_forged_selection():
    result = completed(16, 4, 4)
    result["decision"]["selected"] = completed()["decision"]["selected"]
    with pytest.raises(ValueError, match="gate"):
        runner.eligible_selection(result, select_recipe)


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), 1020, 1200])
def test_shared_heavy_budget_and_fit_reserve_cannot_be_bypassed(seconds):
    with pytest.raises(ValueError):
        runner.remaining_budget(seconds)


def test_budget_subtracts_actual_comparison_time_and_preserves_fit_reserve():
    budget = runner.remaining_budget(323.335223165981)
    assert budget["refit_heavy_seconds"] == pytest.approx(876.664776834019)
    assert budget["training_ceiling_seconds"] == pytest.approx(696.664776834019)
    assert budget["training_ceiling_seconds"] + budget["fit_and_io_reserved_seconds"] == budget["refit_heavy_seconds"]


def test_repeated_refit_stops_before_models_or_verification(tmp_path, monkeypatch):
    (tmp_path / "started.json").write_text("immutable")
    monkeypatch.setattr(runner, "verify", lambda *args: pytest.fail("Repeated refit must not touch models"))
    with pytest.raises(FileExistsError, match="cannot be repeated"):
        runner.run(tmp_path)
    assert (tmp_path / "started.json").read_text() == "immutable"
