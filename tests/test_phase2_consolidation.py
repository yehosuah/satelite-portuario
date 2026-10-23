import importlib.util
import json
from contextlib import closing
from pathlib import Path

import pytest
from shapely.geometry import LineString, MultiPolygon, box, mapping, shape
from test_review import make_review_store

from puertogt.review import add_detection

SPEC = importlib.util.spec_from_file_location(
    "consolidate_phase2_review", Path(__file__).parents[1] / "scripts/consolidate_phase2_review.py")
consolidation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consolidation)


def area_review_batch(tmp_path, monkeypatch, proposed):
    store = make_review_store(tmp_path)
    key = "S2A:MSIL1C:20250103T162701:R040:T15PYR"
    store.register_scene({
        "scene_id": "area-scene", "acquisition_key": key, "sensor": "sentinel-2",
        "product_level": "L1C", "captured_at": "2025-01-03T16:27:01Z",
        "footprint": {}, "port_ids": ["prq"],
    })
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET acquisition_key=? WHERE observation_id='obs'", (key,))
    monkeypatch.setattr(consolidation, "ROOT", tmp_path)
    phase2 = tmp_path / "reports/local-review/phase2"
    phase2.mkdir(parents=True)
    (phase2 / "sampling-plan.json").write_text(json.dumps({"slots": [{
        "selected": {"acquisition_key": key}, "reserves": [], "port_id": "prq",
        "sensor": "sentinel-2", "split": "train",
    }]}))
    (phase2 / "execution-order.json").write_text('{"activated_reserves": []}')
    config = tmp_path / "config"
    config.mkdir()
    (config / "ports.geojson").write_text(json.dumps({"features": [{
        "properties": {"zone_id": "prq_muelles"},
        "geometry": mapping(box(-91, 13, -90, 14)),
    }]}))
    report = phase2 / "review.json"
    report.write_text(json.dumps({"observations": [{
        "observation_id": "obs", "acquisition_key": key, "zone_id": "prq_muelles",
        "port_id": "prq", "sensor": "sentinel-2", "evidence_assets": [],
        "author": "Codex IA second reviewer", "summary": "Explicit reviewed area",
        "proposed_training_reviewed_area_wgs84": mapping(proposed),
    }]}))
    adjudication = phase2 / "adjudication.json"
    adjudication.write_text(json.dumps({
        "author": "Codex IA", "kind": "ai_visual_review", "is_human_review": False,
        "batch": "phase2-area", "sources": [consolidation.pinned(report)],
        "accepted_observation_ids": ["obs"], "exposure": "Raw pixels only",
    }))
    return store, adjudication, phase2 / "plan.json", phase2 / "receipt.json"


def test_clipped_review_applies_all_polygon_area_without_tangent_lines_or_points(tmp_path, monkeypatch):
    inside = MultiPolygon([box(-90.9, 13.1, -90.5, 13.5), box(-90.4, 13.6, -90.2, 13.8)])
    # The two exterior fragments intersect the zone only along a line and a point.
    proposed = MultiPolygon([
        *inside.geoms, box(-91.0001, 13.7, -91, 13.8), box(-90, 14, -89.9999, 14.0001),
    ])
    store, adjudication, plan_path, receipt = area_review_batch(tmp_path, monkeypatch, proposed)
    plan = consolidation.prepare(store, adjudication, plan_path)
    consolidation.apply(store, plan_path, receipt)

    reviewed = shape(plan["actions"][-1]["correction"]["reviewed_area"])
    assert reviewed.geom_type == "MultiPolygon"
    assert reviewed.is_valid
    assert reviewed.equals(inside)
    assert reviewed.area == pytest.approx(0.20)


@pytest.mark.parametrize("proposed", [
    LineString([(-90.9, 13.1), (-90.5, 13.5)]),
    box(-91.0001, 13.7, -91, 13.8),
])
def test_review_without_polygonal_surface_fails_before_plan_is_written(tmp_path, monkeypatch, proposed):
    store, adjudication, plan_path, receipt = area_review_batch(tmp_path, monkeypatch, proposed)
    with pytest.raises(ValueError, match="polygonal surface"):
        consolidation.prepare(store, adjudication, plan_path)
    assert not plan_path.exists()
    assert not receipt.exists()


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
    versioned.write_text(json.dumps({"activated_reserves": [{"reserve_acquisition_key": "new-take",
                                                          "split": "train", "sensor": "sentinel-2",
                                                          "port_ids": ["prq"]}]}))
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


def test_reserve_activation_is_limited_to_its_port_sensor_and_split(tmp_path):
    selection = tmp_path / 'selection.json'
    order = tmp_path / 'order.json'
    selection.write_text(json.dumps({'slots': [
        {'selected': {'acquisition_key': 'primary'}, 'reserves': [{'acquisition_key': 'shared-reserve'}],
         'port_id': port, 'sensor': 'sentinel-2', 'split': split}
        for port, split in [('pbr', 'calibration'), ('stc', 'calibration'), ('pbr', 'test')]]
    }))
    order.write_text(json.dumps({'activated_reserves': [
        {'reserve_acquisition_key': 'shared-reserve', 'port_ids': ['pbr'],
         'sensor': 'sentinel-2', 'split': 'calibration'}]
    }))
    # Use distinct primary keys for the contradictory synthetic test assignment.
    data = json.loads(selection.read_text())
    data['slots'][-1]['selected']['acquisition_key'] = 'test-primary'
    selection.write_text(json.dumps(data))
    active = consolidation.phase2_selection(selection, order)
    assert active[('shared-reserve', 'pbr', 'sentinel-2')]['split'] == 'calibration'
    assert ('shared-reserve', 'stc', 'sentinel-2') not in active
