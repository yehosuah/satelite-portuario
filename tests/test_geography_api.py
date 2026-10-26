import json

from fastapi.testclient import TestClient

from puertogt.api import create_app
from puertogt.store import Store, sha256_file


def test_geography_proposal_is_separate_and_checks_files(tmp_path):
    store = Store(tmp_path / "catalog.sqlite", tmp_path)
    store.initialize()
    ports = tmp_path / "ports.geojson"
    ports.write_text(json.dumps({"type": "FeatureCollection", "features": [], "version": "v1"}))
    v2 = tmp_path / "ports-v2.geojson"
    v2.write_text(json.dumps({"type": "FeatureCollection", "features": [], "version": "v2"}))
    image = tmp_path / "comparison.png"
    image.write_bytes(b"image")
    summary = {"schema": "geography-v2-pilot-summary-v1", "active": False,
               "v1_sha256": sha256_file(ports), "v2_sha256": sha256_file(v2),
               "assets": {"comparison.png": {"sha256": sha256_file(image), "size_bytes": 5}}}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    disabled = TestClient(create_app(store, ports_path=ports))
    assert disabled.get("/v1/geography-proposal").json() == {"available": False, "active": False}
    client = TestClient(create_app(store, ports_path=ports, geography_proposal_dir=tmp_path))
    assert client.get("/v1/geography-proposal").json()["active"] is False
    assert client.get("/v1/ports").json()["version"] == "v1"
    assert client.get("/v1/geography-proposal/images/comparison.png").content == b"image"
    assert client.get("/v1/geography-proposal/images/secret.png").status_code == 422
    image.write_bytes(b"wrong")
    assert client.get("/v1/geography-proposal/images/comparison.png").status_code == 409
    v2.write_text("changed")
    assert client.get("/v1/geography-proposal").status_code == 409
