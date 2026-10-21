import json
from contextlib import closing

import pytest

from puertogt.review import ReviewConflict, record_review
from puertogt.store import Store


def make_review_store(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    store.register_scene(
        dict(
            scene_id="scene",
            acquisition_key="take",
            sensor="sentinel-2",
            product_level="L1C",
            captured_at="2025-01-02T00:00:00Z",
            footprint={},
            port_ids=["prq"],
        )
    )
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,port_id,sensor,captured_at,status,detection_count) VALUES ('obs','take','prq_muelles','test','prq','sentinel-2','2025-01-02T00:00:00.000000Z','usable',1)"
        )
        tx.execute(
            "INSERT INTO detections(detection_id,observation_id,geometry_json,detection_score,created_at) VALUES ('d','obs',?,0.8,'2025-01-02T00:00:00Z')",
            (json.dumps({"type": "Point", "coordinates": [-90.78, 13.92]}),),
        )
    return store


def test_review_preserves_proposal_and_rejects_stale_edit(tmp_path):
    store = make_review_store(tmp_path)
    revision = record_review(store, "d", {"review_status": "rejected"}, "operator")
    with closing(store.connect()) as tx:
        row = tx.execute("SELECT * FROM revisions WHERE revision_id=?", (revision,)).fetchone()
        assert json.loads(row["proposal_json"])["review_status"] == "provisional"
        assert (
            tx.execute("SELECT review_status FROM detections WHERE detection_id='d'").fetchone()[0]
            == "rejected"
        )
    with pytest.raises(ReviewConflict):
        record_review(store, "d", {"review_status": "reviewed"}, "other")
    record_review(
        store, "d", {"review_status": "reviewed"}, "operator", previous_revision_id=revision
    )


def test_dimension_correction_requires_independent_source(tmp_path):
    store = make_review_store(tmp_path)
    with pytest.raises(ValueError, match="provenance"):
        record_review(store, "d", {"length_m": 100}, "operator")


def test_edit_preserves_other_attribute_provenance(tmp_path):
    store = make_review_store(tmp_path)
    provenance = {"kind": "independent_reference", "reference": "source"}
    previous = record_review(
        store, "d", {"family": "carga", "provenance": {"family": provenance}}, "reviewer"
    )
    record_review(
        store,
        "d",
        {"length_m": 100, "provenance": {"length_m": provenance}},
        "reviewer",
        previous_revision_id=previous,
    )
    with closing(store.connect()) as tx:
        data = json.loads(
            tx.execute("SELECT data_json FROM detections WHERE detection_id='d'").fetchone()[0]
        )
    assert set(data["provenance"]) == {"family", "length_m"}


def test_complete_scene_cannot_export_unresolved_proposals(tmp_path):
    from puertogt.review import export_dataset

    store = make_review_store(tmp_path)
    record_review(
        store,
        "obs",
        {"dataset_split": "test", "scene_complete": True},
        "human",
        entity_type="observation",
    )
    with pytest.raises(ValueError, match="provisional"):
        export_dataset(store, tmp_path / "test", split="test", version="1")
    assert not (tmp_path / "test").exists()


def test_export_excludes_test_labels_and_blocks_related_split_leakage(tmp_path):
    from puertogt.review import export_dataset

    store = make_review_store(tmp_path)
    record_review(store, "d", {"review_status": "reviewed"}, "human")
    record_review(
        store,
        "obs",
        {"dataset_split": "test", "scene_complete": True, "split_groups": ["overlap-1"]},
        "human",
        entity_type="observation",
    )
    with pytest.raises(ValueError, match="no reviewed complete scenes"):
        export_dataset(store, tmp_path / "train", split="train", version="v1")
    manifest = export_dataset(store, tmp_path / "test", split="test", version="v1")
    assert manifest["samples"][0]["observation_id"] == "obs"
    with store.transaction() as tx:
        tx.execute(
            "INSERT INTO observations(observation_id,acquisition_key,zone_id,recipe_version,sensor,captured_at,status,review_status,detection_count,data_json) VALUES ('train-obs','take','prq_fondeo','test','sentinel-2','2025-01-02T00:00:00Z','usable','reviewed',0,?)",
            (json.dumps({"dataset_split": "train", "scene_complete": True}),),
        )
    with pytest.raises(ValueError, match="split leakage"):
        export_dataset(store, tmp_path / "leaked", split="train", version="v2")
