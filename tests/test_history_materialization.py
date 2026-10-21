from datetime import datetime, timezone

import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

from puertogt.contracts import Scene
from puertogt.history import HistoryLedger, HistoryLimits, build_tiles, choose_context, create_plan
from puertogt.jobs import JobQueue, ResourceLimit
from puertogt.store import Store


def scene(key="new", sensor="sentinel-2", date="2025-01-05T00:00:00+00:00"):
    return Scene(scene_id=key, acquisition_key=key, sensor=sensor,
                 product_level="L1C" if sensor == "sentinel-2" else "GRDH",
                 captured_at=datetime.fromisoformat(date), footprint=mapping(box(-91, 13, -87, 17)),
                 port_ids=["p"], orbit_direction="ASCENDING", relative_orbit=63,
                 metadata={"purpose": "public_history"})


def zones():
    return [{"type": "Feature", "properties": {"port_id": "p", "zone_id": name},
             "geometry": mapping(geometry)} for name, geometry in [
                 ("long", box(-88.62, 15.69, -88.61, 15.79)),
                 ("overlap", box(-88.62, 15.70, -88.61, 15.71)),
             ]]


def test_tiles_cover_complete_long_zone_with_margins_and_share_overlap():
    tiles = build_tiles(zones(), pixel_size=10, tile_size=512, margin=32)
    assert len({tile["tile_id"] for tile in tiles}) == len(tiles)
    assert all(tile["size"] == 512 and tile["margin"] == 32 for tile in tiles)
    for feature in zones():
        selected = [tile for tile in tiles if feature["properties"]["zone_id"] in tile["zone_ids"]]
        project = Transformer.from_crs(4326, selected[0]["crs"], always_xy=True)
        target = transform(project.transform, shape(feature["geometry"]))
        assert unary_union([box(*tile["core_bounds"]) for tile in selected]).covers(target)
    assert any(len(tile["zone_ids"]) == 2 for tile in tiles)


def test_plan_protects_existing_acquisition_and_keeps_original_data(tmp_path):
    catalog = Store(tmp_path / "catalog.sqlite")
    old, new = scene("old"), scene("new")
    queue = JobQueue(catalog)
    queue.enqueue(old, "long", "old-recipe", port_id="p")
    catalog.register_scene(new)
    with catalog.connect() as tx:
        before = tx.execute("select data_json from acquisitions order by acquisition_key").fetchall()
    plan = create_plan(catalog, zones(), tmp_path / "history")
    assert [item["scene"]["acquisition_key"] for item in plan["acquisitions"]] == ["new"]
    assert len(plan["protected_observation_ids"]) == 1
    assert create_plan(catalog, zones(), tmp_path / "history")["plan_id"] == plan["plan_id"]
    with catalog.connect() as tx:
        assert tx.execute("select data_json from acquisitions order by acquisition_key").fetchall() == before


def test_context_requires_same_track_and_30_to_90_days():
    target = scene("target", "sentinel-1", "2025-03-10T00:00:00+00:00")
    good = scene("good", "sentinel-1", "2025-01-10T00:00:00+00:00")
    wrong = good.model_copy(update={"acquisition_key": "wrong", "relative_orbit": 62})
    recent = good.model_copy(update={"acquisition_key": "recent", "captured_at": datetime(2025, 3, 1, tzinfo=timezone.utc)})
    assert choose_context(target, [wrong, recent, good]).acquisition_key == "good"
    with pytest.raises(ValueError, match="context"):
        choose_context(target, [wrong, recent])


def test_download_budget_is_persistent_and_reserved_before_io(tmp_path):
    limits = HistoryLimits(total_download_bytes=100, acquisition_download_bytes=60, reserve_bytes=0)
    ledger = HistoryLedger(tmp_path, limits)
    plan = {"plan_id": "test", "acquisitions": [{"scene": scene().model_dump(mode="json"), "tiles": []}]}
    ledger.initialize_plan(plan)
    ledger.reserve_download("new", 50)
    reopened = HistoryLedger(tmp_path, limits)
    with pytest.raises(ResourceLimit, match="acquisition"):
        reopened.reserve_download("new", 11)
    assert reopened.summary()["download_bytes_reserved"] == 50


def test_resume_recovers_only_interrupted_plan_rows(tmp_path):
    ledger = HistoryLedger(tmp_path, HistoryLimits(reserve_bytes=0))
    plan = {"plan_id": "test", "acquisitions": [{"scene": scene().model_dump(mode="json"), "tiles": []}]}
    ledger.initialize_plan(plan)
    assert ledger.claim()["scene"]["acquisition_key"] == "new"
    ledger.recover_interrupted()
    row = ledger.claim()
    assert row["scene"]["acquisition_key"] == "new"
    ledger.finish("new", {"tiles": 2})
    ledger.recover_interrupted()
    assert ledger.claim() is None
    assert ledger.summary()["states"] == {"complete": 1}


def test_range_cache_verifies_bytes_and_never_reuses_corruption(tmp_path, monkeypatch):
    from puertogt.history import _RangeSource

    ledger = HistoryLedger(tmp_path, HistoryLimits(reserve_bytes=0))
    ledger.initialize_plan({"plan_id": "test", "acquisitions": [
        {"scene": scene().model_dump(mode="json"), "tiles": []}]})
    calls = []

    class Response:
        status = 206
        headers = {"Content-Range": "bytes 0-3/8"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b"good"

    def request(value, timeout):
        calls.append(value)
        # Reservation was committed before external I/O.
        assert ledger.summary()["download_bytes_reserved"] == 4 * len(calls)
        assert value.get_header("If-match") == '"version"'
        return Response()

    monkeypatch.setattr("puertogt.history.urllib.request.urlopen", request)
    source = _RangeSource({"url": "https://example.org/a.tif", "size": 8, "etag": '"version"'},
                          ledger, "new", tmp_path / "cache")
    assert source.fetch(0, 4) == b"good"
    assert source.fetch(0, 4) == b"good"
    assert len(calls) == 1
    (source.cache / "0-4.bin").write_bytes(b"evil")
    assert source.fetch(0, 4) == b"good"
    assert len(calls) == 2


def test_mosaic_uses_only_nonoverlapping_cores_and_retains_quality(tmp_path):
    import numpy as np
    import rasterio
    from rasterio import Affine

    from puertogt.contracts import RasterBundle
    from puertogt.history import mosaic_zone

    prepared = []
    for index in range(2):
        transform = Affine(10, 0, index * 240 - 40, 0, -10, 280)
        tile = {"tile_id": str(index), "zone_ids": ["zone"], "core_bounds": [index * 240, 0, (index + 1) * 240, 240],
                "size": 32, "margin": 4, "pixel_size": 10}
        paths = {}
        for name in ["B04", "observable"]:
            path = tmp_path / f"{name}-{index}.tif"
            data = np.full((32, 32), 99, dtype="uint16")
            data[4:28, 4:28] = index + 1 if name == "B04" else index
            with rasterio.open(path, "w", driver="GTiff", width=32, height=32,
                               count=1, dtype="uint16", crs="EPSG:32616", transform=transform) as dst:
                dst.write(data, 1)
            paths[name] = path
        bundle = RasterBundle(band_paths={"B04": paths["B04"]}, mask_paths={"observable": paths["observable"]},
                              crs="EPSG:32616", transform=tuple(transform)[:6], units={"B04": "DN_harmonized"},
                              sensor="sentinel-2", product_level="L1C", preprocessing_version="ai2-s2-v1")
        prepared.append((tile, bundle))
    mosaic = mosaic_zone(prepared, "zone", tmp_path / "mosaic")
    with rasterio.open(mosaic.band_paths["B04"]) as source:
        values = source.read(1)
        assert source.shape == (24, 48)
        assert np.all(values[:, :24] == 1) and np.all(values[:, 24:] == 2)
    with rasterio.open(mosaic.mask_paths["observable"]) as source:
        values = source.read(1)
        assert np.all(values[:, :24] == 0) and np.all(values[:, 24:] == 1)
    assert mosaic.metadata["observable_fraction"] == 0.5


def test_publication_rejects_protected_acquisition_even_with_new_recipe(tmp_path):
    from puertogt.history import publish_acquisition

    catalog = Store(tmp_path / "catalog.sqlite")
    old = scene("protected")
    queue = JobQueue(catalog)
    job_id = queue.enqueue(old, "old-zone", "old-recipe", port_id="p")
    protected = {queue.get_job(job_id)["observation_id"]}
    with pytest.raises(ValueError, match="protected pilot acquisition"):
        publish_acquisition(catalog, {"scene": old.model_dump(mode="json")}, [], [], None,
                            protected_ids=protected)
    assert queue.count_jobs() == 1


def test_priority_order_can_change_without_changing_frozen_plan(tmp_path):
    ledger = HistoryLedger(tmp_path, HistoryLimits(reserve_bytes=0))
    ledger.initialize_plan({"plan_id": "test", "acquisitions": [
        {"scene": scene(key).model_dump(mode="json"), "tiles": []} for key in ["a", "b", "c"]]})
    assert ledger.claim(priority_keys=["c", "b"])["scene"]["acquisition_key"] == "c"
    ledger.finish("c", {})
    assert ledger.claim(priority_keys=["b"])["scene"]["acquisition_key"] == "b"
    ledger.finish("b", {})
    assert ledger.claim()["scene"]["acquisition_key"] == "a"


def test_worker_leaves_other_acquisition_pending_and_publishes_only_matching_predictions(tmp_path, monkeypatch):
    import json
    from contextlib import closing

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from puertogt.contracts import ModelManifest, Prediction, RasterBundle
    from puertogt.models import processing_recipe
    from puertogt.worker import process_once

    catalog = Store(tmp_path / "catalog.sqlite")
    geometry = mapping(box(-88.6245, 15.748, -88.623, 15.7495))
    config = tmp_path / "config"
    config.mkdir()
    (config / "ports.geojson").write_text(json.dumps({"features": [
        {"geometry": geometry, "properties": {"zone_id": "zone", "port_id": "p"}}]}))
    monkeypatch.setenv("PUERTOGT_CONFIG_DIR", str(config))
    transform = from_origin(-88.625, 15.75, .0001, .0001)
    path = tmp_path / "numeric.tif"
    with rasterio.open(path, "w", driver="GTiff", width=32, height=32, count=1,
                       crs="EPSG:4326", transform=transform, dtype="uint16") as output:
        output.write(np.ones((32, 32), dtype="uint16"), 1)
    model = ModelManifest(model_id="test", version="v1", sensor="sentinel-2", task="detection", recipe_version="test")
    queue = JobQueue(catalog)
    jobs = {}
    for key in ["other", "wanted"]:
        bundle = RasterBundle(band_paths={band: path for band in ["B04", "B03", "B02"]},
                              mask_paths={"observable": path}, crs="EPSG:4326",
                              transform=tuple(transform)[:6], units={}, preprocessing_version="test",
                              sensor="sentinel-2", product_level="L1C", metadata={"acquisition_key": key})
        target = scene(key).model_copy(update={"metadata": {"prepared_bundle": bundle.model_dump(mode="json")}})
        jobs[key] = queue.enqueue(target, "zone", processing_recipe(model), port_id="p")
    calls = []

    def predict(bundle, manifest):
        calls.append(bundle.metadata["acquisition_key"])
        return [Prediction(prediction_id="wanted-only", geometry=geometry, detection_score=.9)]

    result = process_once(catalog, model, predictor=predict, acquisition_key="wanted")
    assert result["job_id"] == jobs["wanted"]
    assert calls == ["wanted"]
    assert queue.get_job(jobs["other"])["status"] == "pending"
    assert queue.get_job(jobs["wanted"])["status"] == "succeeded"
    with closing(catalog.connect()) as tx:
        assert tx.execute("select o.acquisition_key from detections d join observations o using(observation_id)").fetchone()[0] == "wanted"
