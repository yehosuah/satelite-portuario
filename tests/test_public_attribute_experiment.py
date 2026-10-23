import json
import runpy
from pathlib import Path

import pytest
from test_public_attributes import public_sample, write_selection

from puertogt.store import sha256_file

CHECK_PINS = runpy.run_path(str(Path(__file__).parents[1] / "scripts/train_public_attributes.py"))["check_pins"]


def fixture_plan(tmp_path):
    selection = write_selection(tmp_path, [public_sample()])
    source = tmp_path / "model.py"
    source.write_text("# Frozen fixture model.\n")
    plan = {"schema_version": "public-attribute-experiment-v1", "selection_sha256": sha256_file(selection),
            "source_sha256": {"model.py": sha256_file(source)},
            "training": {"epochs": 50, "learning_rate": 0.001, "batch_size": 16, "seed": 0,
                         "device": "cpu", "cpu_threads": 4, "hyperparameter_search": False},
            "publication": {"automatic_publication": False, "family_validated": False,
                            "length_validated": False, "width_validated": False}}
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(plan))
    return path, selection, source


def test_public_experiment_checks_frozen_sources_before_any_training(tmp_path):
    plan, selection, source = fixture_plan(tmp_path)
    assert CHECK_PINS(tmp_path, plan, selection)["training"]["epochs"] == 50
    source.write_text("# Changed after prespecification.\n")
    with pytest.raises(ValueError, match="source changed"):
        CHECK_PINS(tmp_path, plan, selection)


@pytest.mark.parametrize("change", ["learning_rate", "publication", "selection"])
def test_public_experiment_rejects_changed_recipe_gates_or_selection(tmp_path, change):
    path, selection, _ = fixture_plan(tmp_path)
    plan = json.loads(path.read_text())
    if change == "learning_rate":
        plan["training"]["learning_rate"] = 0.01
    elif change == "publication":
        plan["publication"]["automatic_publication"] = True
    else:
        selection.write_text(selection.read_text() + "\n")
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        CHECK_PINS(tmp_path, path, selection)


def test_performance_amendment_can_change_importer_but_not_model_sources(tmp_path):
    from dataclasses import asdict

    from puertogt.ml.public_attributes import PUBLIC_LIMITS

    path, selection, model_source = fixture_plan(tmp_path)
    name = "src/puertogt/ml/public_attributes.py"
    importer = tmp_path / name
    importer.parent.mkdir(parents=True)
    importer.write_text("# Original importer.\n")
    before = sha256_file(importer)
    plan = json.loads(path.read_text())
    plan["source_sha256"][name] = before
    path.write_text(json.dumps(plan))
    importer.write_text("# Same recipe, four concurrent band transfers.\n")
    limits = asdict(PUBLIC_LIMITS)
    amendment = tmp_path / "performance.json"
    value = {"schema_version": "public-attributes-performance-amendment-v1",
             "selection_sha256": sha256_file(selection), "experiment_plan_sha256": sha256_file(path),
             "limits_before": limits, "limits_after": {**limits, "io_workers": 4},
             "training_started": False, "test_evaluation_started": False,
             "source_changes": {name: {"before": before, "after": sha256_file(importer)}}}
    amendment.write_text(json.dumps(value))
    CHECK_PINS(tmp_path, path, selection, performance_amendment=amendment,
               amendment_sha256=sha256_file(amendment))
    model_source.write_text("# Unexpected model change.\n")
    with pytest.raises(ValueError, match="source changed"):
        CHECK_PINS(tmp_path, path, selection, performance_amendment=amendment,
                   amendment_sha256=sha256_file(amendment))
    value["source_changes"]["model.py"] = {"before": plan["source_sha256"]["model.py"],
                                          "after": sha256_file(model_source)}
    amendment.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="model or preprocessing"):
        CHECK_PINS(tmp_path, path, selection, performance_amendment=amendment,
                   amendment_sha256=sha256_file(amendment))
