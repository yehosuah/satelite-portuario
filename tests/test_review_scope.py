import json
from contextlib import closing

import pytest
from shapely.geometry import box, mapping, shape
from test_review import make_review_store

from puertogt.review import add_detection, export_dataset, record_review


def configured_store(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "ports.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"zone_id": "prq_muelles", "port_id": "prq"},
            "geometry": mapping(box(-91, 13, -90, 14)),
        }],
    }))
    monkeypatch.setenv("PUERTOGT_CONFIG_DIR", str(config))
    return make_review_store(tmp_path)


def test_scope_excludes_ambiguous_proposals_without_turning_them_into_negatives(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    area = mapping(box(-90.6, 13.2, -90.1, 13.6))
    inside = add_detection(store, "obs", mapping(box(-90.5, 13.3, -90.4, 13.4)), "Codex IA")
    outside = add_detection(store, "obs", mapping(box(-90.9, 13.7, -90.8, 13.8)), "Codex IA")
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True, "reviewed_area": area,
        "provenance": {"kind": "ai_visual_review", "reference": "review-v1.json"},
    }, "Codex IA", entity_type="observation")
    manifest = export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    sample = manifest["samples"][0]
    assert shape(sample["reviewed_area"]).equals(shape(area))
    assert sample["reference_provenance"]["kind"] == "ai_visual_review"
    labels = json.loads((tmp_path / "dataset" / sample["labels"]).read_text())
    assert [f["properties"]["id"] for f in labels["features"]] == [inside]
    assert set(sample["excluded_detection_ids"]) == {"d", outside}
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT review_status FROM detections WHERE detection_id='d'").fetchone()[0] == "provisional"


def test_unresolved_proposal_inside_reviewed_area_still_blocks_export(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "obs", {
        "dataset_split": "test", "scene_complete": True,
        "reviewed_area": mapping(box(-91, 13, -90, 14)),
    }, "Codex IA", entity_type="observation")
    with pytest.raises(ValueError, match="provisional"):
        export_dataset(store, tmp_path / "dataset", split="test", version="v1")


def test_unresolved_proposal_crossing_reviewed_boundary_blocks_export(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "d", {
        "geometry": mapping(box(-90.7, 13.3, -90.55, 13.4)),
        "review_status": "provisional",
    }, "Codex IA")
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True,
        "reviewed_area": mapping(box(-90.6, 13.2, -90.1, 13.6)),
    }, "Codex IA", entity_type="observation")

    with pytest.raises(ValueError, match="provisional"):
        export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    assert not (tmp_path / "dataset").exists()


def test_unresolved_proposal_only_touching_reviewed_boundary_does_not_block(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "d", {
        "geometry": mapping(box(-90.7, 13.3, -90.6, 13.4)),
        "review_status": "provisional",
    }, "Codex IA")
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True,
        "reviewed_area": mapping(box(-90.6, 13.2, -90.1, 13.6)),
    }, "Codex IA", entity_type="observation")

    manifest = export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    sample = manifest["samples"][0]
    labels = json.loads((tmp_path / "dataset" / sample["labels"]).read_text())
    assert sample["excluded_detection_ids"] == ["d"]
    assert labels["features"] == []


@pytest.mark.parametrize("review_status", ["reviewed", "rejected"])
def test_resolved_boundary_object_keeps_centroid_membership(tmp_path, monkeypatch, review_status):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "d", {
        "geometry": mapping(box(-90.7, 13.3, -90.55, 13.4)),
        "review_status": review_status,
    }, "Codex IA")
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True,
        "reviewed_area": mapping(box(-90.6, 13.2, -90.1, 13.6)),
    }, "Codex IA", entity_type="observation")

    manifest = export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    sample = manifest["samples"][0]
    labels = json.loads((tmp_path / "dataset" / sample["labels"]).read_text())
    assert sample["excluded_detection_ids"] == ["d"]
    assert labels["features"] == []


def test_reviewed_area_cannot_escape_the_configured_zone(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True,
        "reviewed_area": mapping(box(-92, 13, -90.1, 14)),
    }, "Codex IA", entity_type="observation")
    with pytest.raises(ValueError, match="configured zone"):
        export_dataset(store, tmp_path / "dataset", split="train", version="v1")
    assert not (tmp_path / "dataset").exists()


def test_reviewed_area_must_be_an_area_geometry(tmp_path):
    store = make_review_store(tmp_path)
    with pytest.raises(ValueError, match="Polygon"):
        record_review(store, "obs", {
            "reviewed_area": {"type": "Point", "coordinates": [-90.78, 13.92]},
        }, "Codex IA", entity_type="observation")


def test_explicit_cohort_does_not_silently_include_other_reviewed_observations(tmp_path, monkeypatch):
    store = configured_store(tmp_path, monkeypatch)
    record_review(store, "d", {"review_status": "rejected"}, "Codex IA")
    record_review(store, "obs", {
        "dataset_split": "train", "scene_complete": True,
    }, "Codex IA", entity_type="observation")
    with store.transaction() as tx:
        tx.execute("""INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,
            port_id,sensor,captured_at,status,review_status,detection_count,data_json)
            SELECT 'other',acquisition_key,zone_id,'other-recipe',port_id,sensor,captured_at,
            status,review_status,detection_count,data_json FROM observations WHERE observation_id='obs'""")
    manifest = export_dataset(store, tmp_path / "cohort", split="train", version="v2",
                              observation_ids={"obs"})
    assert [sample["observation_id"] for sample in manifest["samples"]] == ["obs"]
    for requested in ({"absent"}, {"obs", "absent"}, set()):
        with pytest.raises(ValueError, match="cohort"):
            export_dataset(store, tmp_path / "invalid", split="train", version="v2",
                           observation_ids=requested)
        assert not (tmp_path / "invalid").exists()
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET data_json=? WHERE observation_id='other'",
                   (json.dumps({"dataset_split": "test", "scene_complete": True}),))
    with pytest.raises(ValueError, match="split leakage"):
        export_dataset(store, tmp_path / "leaked-cohort", split="train", version="v2",
                       observation_ids={"obs"})
