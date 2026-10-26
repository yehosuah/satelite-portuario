import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from test_review_queue import fixture  # noqa: F401

from puertogt.api import create_app
from puertogt.review_queue import build_review_queue, digest, freeze_queue


@pytest.fixture
def versions(fixture, tmp_path):  # noqa: F811
    store, paths = fixture
    old = build_review_queue(store, *paths)
    archive = tmp_path / "queue-archive"
    freeze_queue(old, archive / f"{old['queue_id']}.json")
    new = copy.deepcopy(old)
    new["entries"][0]["zone_geometry"] = mapping(box(.6, 0, .7, .4))
    new["queue_id"] = "train-" + digest({k: v for k, v in new.items() if k != "queue_id"})[:20]
    active = tmp_path / "active.json"
    freeze_queue(new, active)
    client = TestClient(create_app(store, review_token="test-token", review_queue_path=active,
                                  review_queue_archive_dir=archive))
    return store, client, old, new, archive, active


def test_old_queue_remains_bound_across_active_queue_switch(versions):
    _, client, old, new, _, _ = versions
    assert client.get("/v1/review-queue").json()["queue_id"] == new["queue_id"]
    selected = client.get("/v1/review-queue", params={"queue_id": old["queue_id"]})
    assert selected.status_code == 200
    assert selected.json()["queue_id"] == old["queue_id"]
    detail = client.get("/v1/review-queue/observations/0", params={"queue_id": old["queue_id"]}).json()
    assert detail["queue_id"] == old["queue_id"]
    assert detail["zone_geometry"] == selected.json()["items"][0]["zone_geometry"]
    href = detail["assets"][0]["href"]
    assert href.endswith(f"?queue_id={old['queue_id']}")
    assert client.get(href).content == b"raw"


def test_open_old_tab_can_preview_and_save_its_original_scope(versions):
    store, client, old, new, _, _ = versions
    headers = {"Authorization": "Bearer test-token"}
    area = client.post("/v1/review-queue/geometry", headers=headers, json={
        "queue_id": old["queue_id"], "observation_id": "0", "asset_id": "0-preview",
        "rectangle": [0, 0, 1, 1], "kind": "area",
    })
    assert area.status_code == 200
    body = {"queue_id": old["queue_id"], "inspection_id": str(uuid4()), "observation_id": "0",
            "author": "Test reviewer", "reviewer_kind": "human_visual_review", "decision": "candidates",
            "candidates": [mapping(box(.3, .1, .5, .2))], "reviewed_area": area.json()["geometry"],
            "note": "Draft started before queue switch", "elapsed_seconds": 30}
    saved = client.post("/v1/review-queue/first-pass", headers=headers, json=body)
    assert saved.status_code == 200
    assert client.post("/v1/review-queue/first-pass", headers=headers, json=body).json() == saved.json()
    wrong_scope = {**body, "queue_id": new["queue_id"], "inspection_id": str(uuid4())}
    assert client.post("/v1/review-queue/first-pass", headers=headers, json=wrong_scope).status_code == 422
    receipts = list((store.data_dir / "review-intake").rglob("*.json"))
    assert len(receipts) == 1 and receipts[0].parent.name == old["queue_id"]


def test_archive_cannot_substitute_another_valid_manifest(versions):
    _, client, old, _, archive, active = versions
    (archive / f"{old['queue_id']}.json").write_bytes(active.read_bytes())
    assert client.get("/v1/review-queue", params={"queue_id": old["queue_id"]}).status_code == 409


@pytest.mark.parametrize("contents", [None, [], {"manifest": None}, {"manifest": []}])
def test_malformed_archive_has_a_controlled_integrity_error(versions, contents):
    _, client, old, _, archive, _ = versions
    (archive / f"{old['queue_id']}.json").write_text(json.dumps(contents))
    response = client.get("/v1/review-queue", params={"queue_id": old["queue_id"]})
    assert response.status_code == 409
    assert response.json()["detail"] == "The frozen review queue could not be verified"


def test_archive_tampering_and_paths_fail_closed(versions):
    _, client, old, _, archive, _ = versions
    path = archive / f"{old['queue_id']}.json"
    envelope = json.loads(path.read_text())
    envelope["manifest"]["entries"][0]["zone_id"] = "changed"
    path.write_text(json.dumps(envelope))
    assert client.get("/v1/review-queue", params={"queue_id": old["queue_id"]}).status_code == 409
    assert client.get("/v1/review-queue", params={"queue_id": "../active"}).status_code == 422
    assert client.get("/v1/review-queue", params={"queue_id": "train-" + "0" * 20}).status_code == 409


def test_archive_symlink_must_stay_inside_archive_directory(versions):
    _, client, old, _, archive, active = versions
    path = archive / f"{old['queue_id']}.json"
    path.unlink()
    path.symlink_to(active)
    assert client.get("/v1/review-queue", params={"queue_id": old["queue_id"]}).status_code == 409
