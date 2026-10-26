import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from test_review import make_review_store

from puertogt.api import create_app
from puertogt.review import record_review

TOKEN = "scope-editor-test-credential"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def scope_client(tmp_path, geometry=None):
    store = make_review_store(tmp_path)
    ports = tmp_path / "ports.geojson"
    zone = geometry if geometry is not None else box(-91, 13, -90, 14)
    ports.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": mapping(zone),
        "properties": {"port_id": "prq", "zone_id": "prq_muelles"},
    }]}))
    return store, ports, TestClient(create_app(store, review_token=TOKEN, ports_path=ports))


def preview(client, geometry, observation_id="obs"):
    return client.post("/v1/reviews/scope-preview", headers=HEADERS, json={
        "observation_id": observation_id, "geometry": geometry,
    })


def test_preview_clips_to_zone_keeps_holes_and_never_writes(tmp_path):
    zone = Polygon(box(-91, 13, -90, 14).exterior.coords,
                   [box(-90.8, 13.2, -90.7, 13.3).exterior.coords])
    store, _, client = scope_client(tmp_path, zone)
    previous = record_review(store, "obs", {"note": "existing review"}, "person",
                             entity_type="observation")
    response = preview(client, mapping(box(-91.2, 12.8, -90.4, 13.8)))
    assert response.status_code == 200
    value = response.json()
    expected = zone.intersection(box(-91.2, 12.8, -90.4, 13.8))
    assert shape(value["reviewed_area"]).equals(expected)
    assert len(shape(value["reviewed_area"]).interiors) == 1
    assert value["clipped"] is True
    assert value["previous_revision_id"] == previous
    assert value["observation_id"] == "obs" and value["zone_id"] == "prq_muelles"
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 1
        assert tx.execute("SELECT COUNT(*) FROM detections").fetchone()[0] == 1
    assert "reviewed_area" not in store.get_observation("obs")


def test_preview_preserves_disconnected_parts_and_discards_boundary_only_parts(tmp_path):
    zone = MultiPolygon([box(-91, 13, -90.8, 13.2), box(-90.6, 13.4, -90.4, 13.6),
                         box(-90.2, 13.7, -90, 13.9)])
    _, _, client = scope_client(tmp_path, zone)
    response = preview(client, mapping(box(-91.1, 12.9, -90.2, 14)))
    assert response.status_code == 200
    area = shape(response.json()["reviewed_area"])
    assert area.geom_type == "MultiPolygon"
    assert len(area.geoms) == 2
    assert area.equals(MultiPolygon(list(zone.geoms)[:2]))


@pytest.mark.parametrize("geometry", [
    mapping(box(-92, 13, -91, 14)),  # Only touches the zone boundary.
    mapping(box(-95, 13, -94, 14)),
    {"type": "Point", "coordinates": [-90.5, 13.5]},
    {"type": "Polygon", "coordinates": []},
    {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
    mapping(box(-190, 13, -90.5, 14)),
    {"bad": "geometry"},
])
def test_preview_rejects_unusable_geometry_without_a_revision(tmp_path, geometry):
    store, _, client = scope_client(tmp_path)
    assert preview(client, geometry).status_code == 422
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


def test_preview_requires_auth_matching_zone_and_observable_scene(tmp_path):
    store, ports, client = scope_client(tmp_path)
    body = {"observation_id": "obs", "geometry": mapping(box(-90.8, 13.2, -90.7, 13.3))}
    assert client.post("/v1/reviews/scope-preview", json=body).status_code == 401
    assert client.post("/v1/reviews/scope-preview", json=body,
                       headers={**HEADERS, "Origin": "https://unrelated.invalid"}).status_code == 403
    assert preview(client, body["geometry"], "missing").status_code == 404
    assert preview(client, body["geometry"]).json()["clipped"] is False
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='unusable',detection_count=NULL WHERE observation_id='obs'")
    assert preview(client, body["geometry"]).status_code == 422
    with store.transaction() as tx:
        tx.execute("UPDATE observations SET status='usable',port_id='stc' WHERE observation_id='obs'")
    assert preview(client, body["geometry"]).status_code == 422
    ports.unlink()
    assert preview(client, body["geometry"]).status_code == 422


def test_changed_scope_reopens_completeness_and_preserves_audit(tmp_path):
    store, ports, client = scope_client(tmp_path)
    initial = record_review(store, "obs", {"scene_complete": True}, "person",
                            entity_type="observation")
    area = mapping(box(-90.6, 13.2, -90.4, 13.4))
    correction = {"reviewed_area": area}
    revision = record_review(store, "obs", correction, "person", entity_type="observation",
                             previous_revision_id=initial, ports_path=ports)
    assert correction == {"reviewed_area": area}  # Does not mutate the caller's plan.
    assert store.get_observation("obs")["scene_complete"] is False
    with closing(store.connect()) as tx:
        audit = tx.execute("SELECT proposal_json,correction_json FROM revisions WHERE revision_id=?",
                           (revision,)).fetchone()
    assert json.loads(json.loads(audit["proposal_json"])["data_json"])["scene_complete"] is True
    assert json.loads(audit["correction_json"])["scene_complete"] is False
    body = {"entity_type": "observation", "entity_id": "obs", "author": "person",
            "previous_revision_id": revision, "correction": {"scene_complete": True}}
    assert client.post("/v1/reviews", headers=HEADERS, json=body).status_code == 200
    assert store.get_observation("obs")["scene_complete"] is True
    assert client.post("/v1/reviews", headers=HEADERS, json=body).status_code == 409


def test_scope_save_cannot_bypass_zone_clipping(tmp_path):
    store, _, client = scope_client(tmp_path)
    body = {"entity_type": "observation", "entity_id": "obs", "author": "person",
            "correction": {"reviewed_area": mapping(box(-92, 13, -90.5, 14))}}
    result = client.post("/v1/reviews", headers=HEADERS, json=body)
    assert result.status_code == 422 and "configured zone" in result.json()["detail"]
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


def test_explicit_complete_scope_is_preserved_for_audited_imports(tmp_path):
    store, ports, _ = scope_client(tmp_path)
    record_review(store, "obs", {
        "reviewed_area": mapping(box(-90.6, 13.2, -90.4, 13.4)), "scene_complete": True,
    }, "person", entity_type="observation", ports_path=ports)
    assert store.get_observation("obs")["scene_complete"] is True
