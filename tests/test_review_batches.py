import importlib.util
import json
from contextlib import closing
from pathlib import Path

import pytest
from shapely.geometry import box, mapping
from test_review import make_review_store

from puertogt.review import record_review
from puertogt.store import sha256_file

spec = importlib.util.spec_from_file_location(
    "consolidate_phase2_review", Path(__file__).resolve().parent.parent / "scripts/consolidate_phase2_review.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def fixture_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "ROOT", tmp_path)
    store = make_review_store(tmp_path)
    reference = tmp_path / "review.json"
    reference.write_text('{"author":"AI"}')
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({
        "batch": "test-ai-review", "author": "codex-ai-root",
        "sources": [{"path": "review.json", "sha256": sha256_file(reference)}], "evidence": {},
        "actions": [
            {"entity_type": "addition", "observation_id": "obs", "geometry": mapping(box(-90.8, 13.9, -90.79, 13.91)),
             "provenance": {"kind": "ai_visual_review", "batch": "test-ai-review", "unique_physical_object_key": "take|v1"}},
            {"entity_type": "detection", "entity_id": "d", "correction": {"review_status": "rejected"}},
            {"entity_type": "observation", "entity_id": "obs", "correction": {"scene_complete": True}},
        ],
    }))
    return store, path, tmp_path / "receipt.json", reference


def test_replay_does_not_duplicate_omitted_vessel_or_revisions(tmp_path, monkeypatch):
    store, plan, receipt, _ = fixture_plan(tmp_path, monkeypatch)
    first = batch.apply(store, plan, receipt)
    second = batch.apply(store, plan, receipt)
    assert first["revisions"] == second["revisions"]
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 2
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 3
        added = tx.execute("SELECT detection_score FROM detections WHERE detection_id<>'d'").fetchone()
        assert added[0] is None


@pytest.mark.parametrize("conflict", ["reference", "revision", "receipt"])
def test_conflict_is_detected_before_new_mutations(tmp_path, monkeypatch, conflict):
    store, plan, receipt, reference = fixture_plan(tmp_path, monkeypatch)
    expected_revisions = 0
    if conflict == "reference":
        reference.write_text('{"author":"modified"}')
    elif conflict == "revision":
        record_review(store, "d", {"review_status": "reviewed"}, "other-reviewer")
        expected_revisions = 1
    else:
        receipt.write_text(json.dumps({"plan_sha256": "another-plan"}))
    with pytest.raises(ValueError):
        batch.apply(store, plan, receipt)
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == expected_revisions
