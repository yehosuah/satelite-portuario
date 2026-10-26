import json

from fastapi.testclient import TestClient

from puertogt.api import create_app
from puertogt.store import Store


def client_for(tmp_path):
    ports = tmp_path / "ports.geojson"
    ports.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    return TestClient(create_app(Store(tmp_path / "catalog.sqlite"), ports_path=ports))


def test_pass_coverage_is_read_only_and_distinct_from_accuracy(tmp_path):
    client = client_for(tmp_path)
    response = client.get("/v1/coverage/passes")
    assert response.status_code == 200
    result = response.json()
    assert result["items"] == []
    assert result["zones"] == []
    assert "accuracy" not in result
    assert client.get("/v1/observations").json()["items"] == []


def test_pass_coverage_rejects_invalid_intervals_and_thresholds(tmp_path):
    client = client_for(tmp_path)
    for params in (
        {"start": "2025-02-01T00:00:00Z", "end": "2025-01-01T00:00:00Z"},
        {"start": "2025-02-01T00:00:00"},
        {"min_observable_fraction": 1.1},
        {"min_observable_fraction": -0.1},
        {"sensor": "landsat"},
    ):
        assert client.get("/v1/coverage/passes", params=params).status_code == 422
