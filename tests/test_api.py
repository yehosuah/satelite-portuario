from contextlib import closing

from fastapi.testclient import TestClient

from puertogt.api import create_app
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.store import Store

TOKEN = "local-test-token-not-a-real-credential"


def setup_client(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    for i, status in enumerate(["usable", "unusable", "usable"]):
        scene = dict(
            scene_id=f"s{i}",
            acquisition_key=f"k{i}",
            sensor="sentinel-2",
            product_level="L1C",
            captured_at=f"2025-01-0{i + 1}T00:00:00Z",
            footprint={
                "type": "Polygon",
                "coordinates": [[[-91, 13], [-90, 13], [-90, 14], [-91, 14], [-91, 13]]],
            },
            source_assets={},
            port_ids=["prq"],
        )
        queue.enqueue(scene, "prq_muelles", "test-v1", port_id="prq")
        with store.transaction() as tx:
            tx.execute(
                "UPDATE observations SET status=?,detection_count=? WHERE acquisition_key=?",
                (status, 0 if status == "usable" else None, f"k{i}"),
            )
    return store, TestClient(
        create_app(store, review_token=TOKEN, ports_path=tmp_path / "ports.geojson")
    )


def test_null_count_and_zero_are_distinct_and_period_is_half_open(tmp_path):
    store, client = setup_client(tmp_path)
    response = client.get(
        "/v1/observations", params={"start": "2025-01-01T00:00:00Z", "end": "2025-01-03T00:00:00Z"}
    )
    assert response.status_code == 200
    assert [item["detection_count"] for item in response.json()["items"]] == [0, None]
    assert client.get("/v1/observations", params={"start": "2025-01-01"}).status_code == 422
    assert (
        client.get(
            "/v1/observations",
            params={"start": "2025-01-03T00:00:00Z", "end": "2025-01-02T00:00:00Z"},
        ).status_code
        == 422
    )


def test_keyset_pagination_rejects_cursor_with_changed_filters(tmp_path):
    _, client = setup_client(tmp_path)
    first = client.get("/v1/observations?limit=1").json()
    second = client.get(
        "/v1/observations", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert first["items"][0]["observation_id"] != second["items"][0]["observation_id"]
    assert (
        client.get(
            "/v1/observations", params={"cursor": first["next_cursor"], "status": "usable"}
        ).status_code
        == 422
    )
    assert client.get("/v1/observations?cursor=invalid").status_code == 422


def test_review_requires_auth_and_rejected_observations_are_hidden(tmp_path):
    _, client = setup_client(tmp_path)
    entity = client.get("/v1/observations").json()["items"][0]["observation_id"]
    body = {
        "entity_type": "observation",
        "entity_id": entity,
        "correction": {"review_status": "rejected"},
        "author": "reviewer",
    }
    assert client.post("/v1/reviews", json=body).status_code == 401
    response = client.post("/v1/reviews", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert len(client.get("/v1/observations").json()["items"]) == 2
    assert len(client.get("/v1/observations?include_rejected=true").json()["items"]) == 3
    assert (
        client.post(
            "/v1/reviews", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 409
    )


def test_assets_require_registered_confined_file_and_jobs_hide_credentials(tmp_path):
    store, client = setup_client(tmp_path)
    assert client.get("/v1/assets/unknown").status_code == 404
    with closing(store.connect()) as tx:
        job = tx.execute("SELECT job_id FROM jobs LIMIT 1").fetchone()[0]
    with store.transaction() as tx:
        tx.execute(
            "UPDATE jobs SET error_message=?,lease_token=? WHERE job_id=?",
            ("https://source?token=secret", "secret", job),
        )
    response = client.get("/v1/jobs").text
    assert "secret" not in response
    assert client.get("/health").json()["status"] == "ok"
