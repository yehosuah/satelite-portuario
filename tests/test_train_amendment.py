import importlib.util
import json
import sys
from pathlib import Path

import pytest

from puertogt.store import sha256_file

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("train_phase2_amendment", SCRIPTS / "train_phase2_amendment.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def sample(day, observation):
    key = f"S2A:MSIL1C:2025{day}T162701:R040:T15PYR"
    return {"observation_id": observation, "acquisition_key": key,
            "pass_id": f"S2A:2025{day}T162701:R040", "sensor": "sentinel-2",
            "captured_at": f"2025-{day[:2]}-{day[2:]}T16:27:01Z", "split_groups": [key],
            "labels": f"{observation}.json", "sha256": "pending", "evidence_assets": []}


def source(root, split="train"):
    root.mkdir()
    rows = [sample("0103", "jan"), sample("0202", "feb-positive"), sample("0217", "feb-negative")]
    acquisitions = []
    for index, row in enumerate(rows):
        label = root / row["labels"]
        label.write_text(json.dumps({"features": [] if index == 2 else [{"positive": True}]}))
        row["sha256"] = sha256_file(label)
        halo = root / f"halo-{index}.bin"
        halo.write_bytes(bytes([index]) * 16)
        acquisitions.append({"acquisition_key": row["acquisition_key"], "pass_id": row["pass_id"],
                             "expected_tile_ids": [f"tile-{index}"], "tiles": [{
                                 "tile": {"tile_id": f"tile-{index}", "margin": 32},
                                 "file_sha256": {halo.name: sha256_file(halo)}}]})
    value = {"split": split, "version": "source", "samples": rows,
             "history_inference": {"version": "history-tiles-v1", "acquisitions": acquisitions}}
    (root / "manifest.json").write_text(json.dumps(value))
    return value


def test_temporal_fold_retains_all_february_negatives_and_halos(tmp_path):
    root = tmp_path / "source"
    original = source(root)
    before = {p.name: sha256_file(p) for p in root.iterdir()}
    output = tmp_path / "fold"
    folded = runner.freeze_temporal_fold(root, output, ["2025-02-01", "2025-03-01"])
    assert [r["observation_id"] for r in folded["samples"]] == ["feb-positive", "feb-negative"]
    assert folded["split"] == "train"
    assert folded["development_partition"] == "temporal_validation"
    assert folded["history_inference"]["acquisitions"] == original["history_inference"]["acquisitions"][1:]
    assert json.loads((output / "feb-negative.json").read_text())["features"] == []
    assert sha256_file(output / "halo-1.bin") == sha256_file(root / "halo-1.bin")
    assert not (output / "jan.json").exists()
    assert before == {p.name: sha256_file(p) for p in root.iterdir()}


@pytest.mark.parametrize("split", ["test", "calibration"])
def test_temporal_fold_refuses_reserved_splits_before_copy(tmp_path, split):
    root = tmp_path / "source"
    source(root, split)
    with pytest.raises(ValueError, match="TRAIN"):
        runner.freeze_temporal_fold(root, tmp_path / "output", ["2025-02-01", "2025-03-01"])
    assert not (tmp_path / "output").exists()


def test_inner_partition_rejects_same_physical_pass_and_numeric_leakage():
    left = {"ids": {"one"}, "groups": {"pass-1"}, "numeric_hashes": {"a"}}
    right = {"ids": {"two"}, "groups": {"pass-1"}, "numeric_hashes": {"b"}}
    with pytest.raises(ValueError, match="groups"):
        runner.assert_disjoint(left, right)
    right["groups"] = {"pass-2"}
    right["numeric_hashes"] = {"a"}
    with pytest.raises(ValueError, match="numeric_hashes"):
        runner.assert_disjoint(left, right)


def candidate(name, tp, fp, fn, epochs=3, lr=2e-5):
    return {"name": name, "epochs": epochs, "learning_rate": lr,
            "overall": {"true_positives": tp, "false_positives": fp, "false_negatives": fn}}


def test_selection_uses_fixed_f1_recall_gate_and_predeclared_ties():
    rows = [candidate("original", 4, 4, 6), candidate("high_precision_low_recall", 3, 0, 7),
            candidate("long", 7, 2, 3, epochs=12), candidate("short", 7, 2, 3)]
    selected = runner.select_recipe(rows)
    assert selected["selected"]["name"] == "short"
    assert selected["selected"]["f1"] == pytest.approx(14 / 19)
    assert selected["automatic_publication"] is False
    assert runner.select_recipe([candidate("original", 9, 1, 1), candidate("equal", 9, 1, 1)])["selected"] is None
    assert runner.select_recipe([candidate("original", 9, 20, 1), candidate("less_recall", 8, 0, 2)])["selected"] is None


def test_missing_temporal_halo_fails_without_publishing_fold(tmp_path):
    root = tmp_path / "source"
    source(root)
    (root / "halo-1.bin").unlink()
    with pytest.raises(FileNotFoundError):
        runner.freeze_temporal_fold(root, tmp_path / "output", ["2025-02-01", "2025-03-01"])
    assert not (tmp_path / "output").exists()
