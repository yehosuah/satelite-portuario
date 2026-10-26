import json
from contextlib import closing
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from test_review_queue import fixture  # noqa: F401

from puertogt.api import create_app
from puertogt.review_queue import build_review_queue, freeze_queue

HEADERS = {"Authorization": "Bearer intake-test-token"}


@pytest.fixture
def intake(fixture, tmp_path):  # noqa: F811
    store, paths = fixture
    manifest = build_review_queue(store, *paths)
    path = tmp_path / "active-queue.json"
    freeze_queue(manifest, path)
    client = TestClient(create_app(store, review_token="intake-test-token", review_queue_path=path))
    entry = manifest["entries"][0]
    body = {"queue_id": manifest["queue_id"], "inspection_id": str(uuid4()),
            "observation_id": entry["observation_id"], "author": "Test reviewer",
            "reviewer_kind": "ai_visual_review", "decision": "candidates",
            "candidates": [mapping(box(.3, .1, .5, .2))], "reviewed_area": entry["zone_geometry"],
            "note": "Potential vessel, still requires adjudication", "elapsed_seconds": 40}
    return store, client, body


def test_blind_api_never_reads_model_outputs_or_exposes_prior_labels(intake):
    store, client, body = intake
    queue = client.get("/v1/review-queue")
    assert queue.status_code == 200 and queue.json()["available"] is True
    observation = client.get(f"/v1/review-queue/observations/{body['observation_id']}")
    assert observation.status_code == 200
    encoded = queue.text + observation.text
    assert all(term not in encoded for term in ("secret", "score", "detection_count", "reviewed_area", "model_id"))
    assert client.get("/v1/review-queue/observations/3").status_code == 404
    image = observation.json()["assets"][0]["href"]
    assert client.get(image).content == b"raw"
    (store.data_dir / "0/preview.png").write_bytes(b"changed")
    assert client.get(image).status_code == 409


def test_first_pass_is_idempotent_and_is_not_a_training_label(intake):
    store, client, body = intake
    assert client.post("/v1/review-queue/first-pass", json=body).status_code == 401
    result = client.post("/v1/review-queue/first-pass", headers=HEADERS, json=body)
    assert result.status_code == 200
    assert client.post("/v1/review-queue/first-pass", headers=HEADERS, json=body).json() == result.json()
    assert result.json()["is_training_label"] is False
    assert result.json()["is_independent_validation"] is False
    receipts = list((store.data_dir / "review-intake").rglob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["submission"] == json.loads(json.dumps(body))
    with closing(store.connect()) as tx:
        assert tx.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0
        assert tx.execute("SELECT review_status FROM observations WHERE observation_id='0'").fetchone()[0] == "provisional"
    body["note"] = "Cannot replace the frozen first pass"
    assert client.post("/v1/review-queue/first-pass", headers=HEADERS, json=body).status_code == 409


@pytest.mark.parametrize("change", [
    {"candidates": []}, {"decision": "empty"}, {"reviewed_area": None},
    {"candidates": [mapping(box(5, 5, 6, 6))]},
    {"reviewed_area": mapping(box(-1, -1, 1, 1))},
    {"decision": "uncertain", "note": ""}, {"inspection_id": "../unsafe"},
    {"reviewer_kind": "validated_human"}, {"elapsed_seconds": -1},
])
def test_invalid_findings_do_not_create_receipts(intake, change):
    store, client, body = intake
    result = client.post("/v1/review-queue/first-pass", headers=HEADERS, json={**body, **change})
    assert result.status_code == 422
    assert not (store.data_dir / "review-intake").exists()


def test_uncertainty_and_queue_conflict_are_explicit(intake):
    _, client, body = intake
    body.update(decision="uncertain", candidates=[], reviewed_area=None, note="Image ambiguous")
    assert client.post("/v1/review-queue/first-pass", headers=HEADERS, json=body).status_code == 200
    body["queue_id"] = "train-" + "0" * 20
    assert client.post("/v1/review-queue/first-pass", headers=HEADERS, json=body).status_code == 409


def test_queue_geometry_clips_area_without_reading_proposals(intake):
    _, client, body = intake
    drawing = {"queue_id": body["queue_id"], "observation_id": body["observation_id"],
               "asset_id": "0-preview", "rectangle": [0, 0, 1, 1], "kind": "area"}
    assert client.post("/v1/review-queue/geometry", json=drawing).status_code == 401
    response = client.post("/v1/review-queue/geometry", headers=HEADERS, json=drawing)
    assert response.status_code == 200
    from shapely.geometry import shape

    assert shape(response.json()["geometry"]).equals(shape(body["reviewed_area"]))
    for rectangle in ([0, 0, 0, 1], [-1, 0, 1, 1], [0.8, 0.2, 0.1, 0.9]):
        assert client.post("/v1/review-queue/geometry", headers=HEADERS,
                           json={**drawing, "rectangle": rectangle}).status_code == 422
    assert client.post("/v1/review-queue/geometry", headers=HEADERS,
                       json={**drawing, "asset_id": "0-numeric"}).status_code == 404


def test_native_projection_does_not_stretch_latitude_bounds():
    from puertogt.review_intake import preview_projection

    # Known Web Mercator y at latitude60 differs materially from the linear WGS84 midpoint.
    to_pixel, to_world = preview_projection({}, {
        "crs": "EPSG:3857", "bounds": [0, 0, 111319.49079327357, 8399737.889818357],
    })
    _, latitude = to_world(.5, .5)
    assert latitude == pytest.approx(35.26438968275465)
    assert to_pixel(.5, latitude) == pytest.approx((.5, .5))
