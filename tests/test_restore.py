import json
import sqlite3
from contextlib import closing

import pytest

from puertogt.export import export_bundle, export_geojson, export_parquet, restore_bundle
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.store import Store, sha256_file


@pytest.fixture
def populated_store(tmp_path):
    store = Store(tmp_path / "live" / "catalog.sqlite")
    scene = {
        "scene_id": "safe", "acquisition_key": "one-take", "sensor": "sentinel-2",
        "product_level": "L1C", "captured_at": "2026-09-02T10:00:00Z", "port_ids": ["quetzal"],
        "source_assets": {"B04": "https://user:password@example.test/image.tif?token=ABC-secret"},
        "metadata": {"api_token": "never-export-me", "ordinary": "kept"},
    }
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    job_id = queue.enqueue(scene, "prq_muelles", "s2-v1")
    job = queue.claim("worker")
    queue.publish_asset(job_id, job["lease_token"], b"numeric evidence", "assets/one.bin")
    queue.complete(job_id, job["lease_token"], status="usable", observable_fraction=1,
                   detections=[{"prediction_id": "d1", "geometry": {"type": "Point", "coordinates": [-90.78, 13.92]},
                                "detection_score": 0.92, "evidence_asset_ids": ["reference"]}])
    (store.data_dir / "models").mkdir()
    (store.data_dir / "models/model.bin").write_bytes(b"own weights")
    store.register_model({"model_id": "s2-model", "version": "v1", "task": "detection",
                          "sensor": "sentinel-2", "recipe_version": "s2-v1"}, weights_path="models/model.bin")
    (store.data_dir / ".env").write_text("TOKEN=keep-local")
    return store


def test_portable_snapshot_restores_assets_models_and_queries(populated_store, tmp_path):
    bundle = tmp_path / "bundle"
    manifest = export_bundle(populated_store, bundle, config={"storage": {"reserve_bytes": 100}, "token": "DO-NOT-COPY"})
    assert set(manifest.files) == {"catalog.sqlite", "assets/one.bin", "models/model.bin", "config.json"}
    restored = restore_bundle(bundle, tmp_path / "restored")
    with closing(restored.connect()) as connection:
        assert connection.execute("SELECT count(*) FROM observations WHERE status='usable'").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM detections").fetchone()[0] == 1
        asset = connection.execute("SELECT * FROM assets").fetchone()
        model = connection.execute("SELECT * FROM models").fetchone()
        acquisition = connection.execute("SELECT * FROM acquisitions").fetchone()
    assert restored.asset_path(asset["asset_id"]).read_bytes() == b"numeric evidence"
    assert (restored.data_dir / model["weights_path"]).read_bytes() == b"own weights"
    assert json.loads(acquisition["source_assets_json"])["B04"] == "https://example.test/image.tif"
    assert "never-export-me" not in acquisition["data_json"]
    assert "DO-NOT-COPY" not in (bundle / "config.json").read_text()
    assert not (bundle / ".env").exists()
    assert b"never-export-me" not in (bundle / "catalog.sqlite").read_bytes()
    assert b"ABC-secret" not in (bundle / "catalog.sqlite").read_bytes()
    # Sanitizing the portable snapshot must never alter the live credentials.
    with closing(populated_store.connect()) as connection:
        assert "ABC-secret" in connection.execute("SELECT source_assets_json FROM acquisitions").fetchone()[0]


def test_tampered_file_aborts_restore_without_partial_destination(populated_store, tmp_path):
    bundle = tmp_path / "bundle"
    export_bundle(populated_store, bundle)
    (bundle / "assets/one.bin").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        restore_bundle(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("unsafe", ["../outside", "/absolute", "assets/../../outside", "C:\\outside"])
def test_manifest_path_traversal_is_rejected(populated_store, tmp_path, unsafe):
    bundle = tmp_path / "bundle"
    export_bundle(populated_store, bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][unsafe] = "a" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore_bundle(bundle, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_symlink_and_catalog_path_outside_manifest_are_rejected(populated_store, tmp_path):
    bundle = tmp_path / "bundle"
    export_bundle(populated_store, bundle)
    evidence = bundle / "assets/one.bin"
    evidence.unlink()
    evidence.symlink_to(populated_store.data_dir / "assets/one.bin")
    with pytest.raises(ValueError, match="Symlinks"):
        restore_bundle(bundle, tmp_path / "restored")
    evidence.unlink()
    evidence.write_bytes(b"numeric evidence")
    with sqlite3.connect(bundle / "catalog.sqlite") as connection:
        connection.execute("UPDATE assets SET path='../outside'")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["catalog.sqlite"] = sha256_file(bundle / "catalog.sqlite")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        restore_bundle(bundle, tmp_path / "restored")


def test_export_checks_registered_file_checksums(populated_store, tmp_path):
    (populated_store.data_dir / "models/model.bin").write_bytes(b"changed weights")
    with pytest.raises(ValueError, match="checksum"):
        export_bundle(populated_store, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_geojson_and_parquet_preserve_geometry_and_nulls(populated_store, tmp_path):
    geojson = export_geojson(populated_store, tmp_path / "detections.geojson")
    feature = json.loads(geojson.read_text())["features"][0]
    assert feature["geometry"]["coordinates"] == [-90.78, 13.92]
    assert feature["properties"]["length_m"] is None
    parquet = export_parquet(populated_store, tmp_path / "detections.parquet")
    import pyarrow.parquet as pq
    table = pq.read_table(parquet)
    assert table.num_rows == 1
    assert table.column("length_m").to_pylist() == [None]
    assert json.loads(table.column("geometry").to_pylist()[0])["coordinates"] == [-90.78, 13.92]


def test_exports_exclude_predictions_when_parent_observation_is_rejected(populated_store, tmp_path):
    with populated_store.transaction() as connection:
        connection.execute("UPDATE observations SET review_status='rejected'")
    result = export_geojson(populated_store, tmp_path / "rejected.geojson")
    assert json.loads(result.read_text())["features"] == []
