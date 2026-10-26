import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from puertogt.store import sha256_file

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("train_anchor_comparison", SCRIPTS / "train_anchor_comparison.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def plan():
    return json.loads((SCRIPTS.parent / "reports/local-review/phase3-diagnosis/anchor-comparison-plan.json").read_text())


@pytest.mark.parametrize("partition", ["training", "development", "refit"])
def test_reserved_partition_is_rejected_before_data_access(partition):
    value = plan()
    value[partition]["split"] = "test"
    with pytest.raises(ValueError, match="TRAIN"):
        runner.validate_plan(value)


def test_freeze_preserves_exact_preregistered_bytes(tmp_path, monkeypatch):
    value = plan()
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")
    digest = sha256_file(source)
    fake = SimpleNamespace(weights_sha256=value["fixed"]["public_initialization_sha256"],
                           model_dump=lambda **kwargs: {"fixture": True})
    monkeypatch.setattr(runner, "read_model", lambda *args: fake)
    monkeypatch.setattr(runner, "validate_inputs", lambda *args: None)
    monkeypatch.setattr(runner, "source_hashes", lambda: {})
    monkeypatch.setattr(runner, "runtime_identity", lambda: {})
    monkeypatch.setattr(runner, "archive_sources", lambda *args: {})
    runner.freeze(source, digest, tmp_path / "frozen")
    assert sha256_file(tmp_path / "frozen/plan.json") == digest
    assert json.loads((tmp_path / "frozen/freeze.json").read_text())["plan_sha256"] == digest
    with pytest.raises(FileExistsError):
        runner.freeze(source, digest, tmp_path / "frozen")


@pytest.mark.parametrize("existing", ["started.json", "completed.json", "failed.json"])
def test_second_execution_fails_before_input_or_model_access(tmp_path, monkeypatch, existing):
    marker = tmp_path / existing
    marker.write_bytes(b"immutable attempt")

    def forbidden(*args):
        pytest.fail("A repeated attempt must stop before reading or training models")

    monkeypatch.setattr(runner, "verify_frozen", forbidden)
    monkeypatch.setattr(runner, "train_detector", forbidden)
    with pytest.raises(FileExistsError, match="cannot be repeated"):
        runner.run(tmp_path)
    assert marker.read_bytes() == b"immutable attempt"
