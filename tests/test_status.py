import json
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from puertogt.api import create_app
from puertogt.cli import doctor
from puertogt.store import Store


def test_system_status_distinguishes_missing_and_unreadable_campaigns(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    client = TestClient(create_app(store))
    empty = client.get("/v1/system").json()
    assert empty["history"] == []
    assert empty["observations"]["total"] == 0
    assert len(empty["model_tasks"]) == 5
    assert not any(task["local_validation_declared"] for task in empty["model_tasks"])

    campaign = tmp_path / "history" / "broken"
    campaign.mkdir(parents=True)
    (campaign / "progress.sqlite").write_text("not a database; secret")
    broken = client.get("/v1/system").json()["history"][0]
    assert broken == {"campaign": "broken", "readable": False,
                      "error": "campaign_status_unavailable"}


def test_campaign_totals_keep_missing_and_failed_inputs_visible_without_leaking(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    campaign = tmp_path / "history" / "full-v1"
    campaign.mkdir(parents=True)
    path = campaign / "progress.sqlite"
    with closing(sqlite3.connect(path)) as tx:
        tx.execute("CREATE TABLE history_acquisitions(status TEXT, started_at TEXT, "
                   "finished_at TEXT, error_message TEXT)")
        tx.executemany("INSERT INTO history_acquisitions VALUES(?,?,?,?)", [
            (status, "2025-01-01T00:00:00Z", None, "https://host?token=secret")
            for status in ["complete", "complete", "retry", "failed", "unavailable"]
        ])
        tx.commit()
    (campaign / "PAUSE").touch()
    client = TestClient(create_app(store))
    response = client.get("/v1/system")
    assert response.status_code == 200
    assert "secret" not in response.text and str(tmp_path) not in response.text
    status = response.json()["history"][0]
    assert (status["total"], status["complete"], status["outstanding"], status["unavailable"]) == (5, 2, 2, 1)
    assert status["pause_requested"] is True
    assert status["counts"]["failed"] == 1
    with closing(sqlite3.connect(path)) as tx:
        assert tx.execute("SELECT count(*) FROM history_acquisitions").fetchone()[0] == 5


def test_model_registration_alone_does_not_declare_local_validation(tmp_path):
    store = Store(tmp_path / "catalog.sqlite")
    manifest = {"model_id": "demo", "task": "detection", "sensor": "sentinel-2",
                "publication_policy": {"status": "validated"}, "weights_sha256": "abc"}
    with store.transaction() as tx:
        tx.execute("INSERT INTO models(model_id,task,sensor,version,created_at,data_json) "
                   "VALUES(?,?,?,?,?,?)", ("demo", "detection", "sentinel-2", "v1",
                                          "2025-01-01", json.dumps(manifest)))
    client = TestClient(create_app(store))
    tasks = client.get("/v1/system").json()["model_tasks"]
    assert not any(task["local_validation_declared"] for task in tasks)
    manifest["dataset_version"] = "public-oscd-only"
    with store.transaction() as tx:
        tx.execute("UPDATE models SET data_json=?", (json.dumps(manifest),))
    tasks = client.get("/v1/system").json()["model_tasks"]
    assert not any(task["local_validation_declared"] for task in tasks)
    assert "Independent local validation and own weights: detection/sentinel-2" in doctor(store)["missing_evidence"]
    manifest["publication_policy"]["local_validation"] = "passed"
    manifest["dataset_version"] = "local-test-v1"
    with store.transaction() as tx:
        tx.execute("UPDATE models SET data_json=?", (json.dumps(manifest),))
    tasks = client.get("/v1/system").json()["model_tasks"]
    assert sum(task["local_validation_declared"] for task in tasks) == 1
    assert "Independent local validation and own weights: detection/sentinel-2" not in doctor(store)["missing_evidence"]
