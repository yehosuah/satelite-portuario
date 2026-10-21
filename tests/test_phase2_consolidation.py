import importlib.util
import json
from contextlib import closing
from pathlib import Path

import pytest
from shapely.geometry import box, mapping
from test_review import make_review_store

from puertogt.review import add_detection

SPEC = importlib.util.spec_from_file_location(
    "consolidate_phase2_review", Path(__file__).parents[1] / "scripts/consolidate_phase2_review.py")
consolidation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consolidation)


def test_conflicting_omitted_object_is_detected_before_any_prior_review_is_written(tmp_path):
    store = make_review_store(tmp_path)
    provenance = {"batch": "phase2", "unique_physical_object_key": "take|one"}
    add_detection(store, "obs", mapping(box(-90.8, 13.8, -90.7, 13.9)), "Codex IA", provenance=provenance)
    plan = {"batch": "phase2", "author": "Codex IA", "sources": [], "evidence": {}, "actions": [
        {"entity_type": "detection", "entity_id": "d", "correction": {"review_status": "rejected"}},
        {"entity_type": "addition", "observation_id": "obs", "provenance": provenance,
         "geometry": mapping(box(-90.7, 13.8, -90.6, 13.9))},
    ]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="omitted object differs"):
        consolidation.apply(store, path, tmp_path / "receipt.json")
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT review_status FROM detections WHERE detection_id='d'").fetchone()[0] == "provisional"
        assert tx.execute("SELECT count(*) FROM revisions WHERE entity_id='d'").fetchone()[0] == 0
    assert not (tmp_path / "receipt.json").exists()


def test_versioned_execution_order_preserves_original_batch_replay(tmp_path, monkeypatch):
    store = make_review_store(tmp_path)
    monkeypatch.setattr(consolidation, "ROOT", tmp_path)
    phase2 = tmp_path / "reports/local-review/phase2"
    phase2.mkdir(parents=True)
    selection = {"slots": [{"selected": {"acquisition_key": "take"}, "reserves": [],
                            "port_id": "prq", "sensor": "sentinel-2", "split": "train"}]}
    (phase2 / "sampling-plan.json").write_text(json.dumps(selection))
    original = phase2 / "execution-order.json"
    original.write_text(json.dumps({"activated_reserves": []}))
    config = tmp_path / "config"
    config.mkdir()
    (config / "ports.geojson").write_text(json.dumps({"features": [{
        "properties": {"zone_id": "prq_muelles"},
        "geometry": mapping(box(-91, 13, -90, 14)),
    }]}))
    report = phase2 / "review.json"
    report.write_text(json.dumps({"observations": [{
        "observation_id": "obs", "acquisition_key": "take", "zone_id": "prq_muelles",
        "port_id": "prq", "sensor": "sentinel-2", "evidence_assets": [],
        "author": "Codex IA second reviewer", "summary": "Unobservable area excluded",
    }]}))
    adjudication = phase2 / "adjudication.json"
    adjudication.write_text(json.dumps({
        "author": "Codex IA", "kind": "ai_visual_review", "is_human_review": False,
        "batch": "phase2-order-v1", "sources": [consolidation.pinned(report)],
        "accepted_observation_ids": ["obs"], "exposure": "Raw pixels only",
    }))
    first_plan = phase2 / "plan-v1.json"
    first = consolidation.prepare(store, adjudication, first_plan)
    receipt = phase2 / "receipt-v1.json"
    applied = consolidation.apply(store, first_plan, receipt)
    original_hash = consolidation.sha256_file(original)
    versioned = phase2 / "execution-order-v2.json"
    versioned.write_text(json.dumps({"activated_reserves": [{"reserve_acquisition_key": "new-take"}]}))
    second = consolidation.prepare(store, adjudication, phase2 / "plan-v2.json", order_path=versioned)
    assert consolidation.pinned(original) in first["sources"]
    assert consolidation.pinned(versioned) in second["sources"]
    replay = consolidation.apply(store, first_plan, receipt)
    assert replay["revisions"] == applied["revisions"]
    assert consolidation.sha256_file(original) == original_hash
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1


def test_duplicate_entity_actions_fail_before_writing_any_review(tmp_path):
    store = make_review_store(tmp_path)
    plan = {"batch": "phase2", "author": "Codex IA", "sources": [], "evidence": {}, "actions": [
        {"entity_type": "detection", "entity_id": "d", "correction": {"review_status": "reviewed"}},
        {"entity_type": "detection", "entity_id": "d", "correction": {"review_status": "rejected"}},
    ]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="Duplicate review entity"):
        consolidation.apply(store, path, tmp_path / "receipt.json")
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0
        assert tx.execute("SELECT review_status FROM detections WHERE detection_id='d'").fetchone()[0] == "provisional"
    assert not (tmp_path / "receipt.json").exists()


def test_review_written_after_preflight_is_not_silently_accepted(tmp_path, monkeypatch):
    store = make_review_store(tmp_path)
    plan = {"batch": "phase2", "author": "Codex IA", "sources": [], "evidence": {}, "actions": [
        {"entity_type": "detection", "entity_id": "d", "correction": {"review_status": "reviewed"}},
        {"entity_type": "observation", "entity_id": "obs", "correction": {"review_status": "reviewed"}},
    ]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    original = consolidation.record_review

    def concurrent_write(*args, **kwargs):
        revision = original(*args, **kwargs)
        original(store, "obs", {"review_status": "rejected"}, "Other reviewer", entity_type="observation")
        return revision

    monkeypatch.setattr(consolidation, "record_review", concurrent_write)
    with pytest.raises(ValueError, match="different review already exists"):
        consolidation.apply(store, path, tmp_path / "receipt.json")
    assert not (tmp_path / "receipt.json").exists()
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT review_status FROM observations WHERE observation_id='obs'").fetchone()[0] == "rejected"
