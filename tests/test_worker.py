import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine, from_origin

from puertogt.contracts import ModelManifest, Prediction, RasterBundle, Scene
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.models import processing_recipe
from puertogt.store import Store
from puertogt.worker import process_once, resolve_bundle_paths


def test_bundle_resolution_keeps_existing_paths_and_bundle_file_precedence(tmp_path, monkeypatch):
    bundle_dir, working, volume = (tmp_path / name for name in ("bundles", "working", "volume"))
    relative = Path("data/pilot/band.tif")
    for directory in (bundle_dir, working, volume):
        (directory / relative).parent.mkdir(parents=True)
        (directory / relative).write_bytes(directory.name.encode())
    monkeypatch.chdir(working)
    bundle = RasterBundle(
        band_paths={"B04": relative},
        crs="EPSG:4326",
        transform=(1, 0, 0, 0, -1, 0),
        units={"B04": "DN_harmonized"},
        preprocessing_version="test-v1",
        sensor="sentinel-2",
        product_level="L1C",
    )
    from_file = resolve_bundle_paths(bundle, volume, relative_to=bundle_dir)
    assert from_file.band_paths["B04"] == bundle_dir / relative
    from_working = resolve_bundle_paths(bundle, volume)
    assert from_working.band_paths["B04"] == working / relative
    assert bundle.band_paths["B04"] == relative

    bundle.band_paths["B04"] = volume / relative
    absolute = resolve_bundle_paths(bundle, volume, relative_to=bundle_dir)
    assert absolute.band_paths["B04"] == volume / relative


@pytest.mark.parametrize(
    "zone_bounds,skew,expected_status,expected_fraction",
    [
        pytest.param(
            (-90.796875, 13.920654296875, -90.78857421875, 13.928955078125),
            False,
            "usable",
            1.0,
            id="whole-zone-with-pixel-centre-boundary",
        ),
        pytest.param(
            (-90.796875, 13.921875, -90.765625, 13.9296875),
            False,
            "partial",
            0.5,
            id="half-zone-outside-crop",
        ),
        pytest.param(
            (-90.8125, 13.921875, -90.796875, 13.9375),
            True,
            "partial",
            0.5,
            id="half-zone-outside-skewed-footprint",
        ),
    ],
)
def test_worker_reads_prepared_bundle_from_custom_volume_in_another_directory(
    tmp_path, monkeypatch, zone_bounds, skew, expected_status, expected_fraction
):
    store = Store(tmp_path / "mounted-volume" / "catalog.sqlite")
    prepared = store.data_dir / "pilot" / "prepared"
    prepared.mkdir(parents=True)
    # Binary-exact coordinates make geometric coverage independent of rounding.
    # The first zone has area 72.25 pixels, with an edge on pixel centres;
    # a rasterized pixel count must not turn its fully covered area into a gap.
    affine = Affine(1 / 1024, 1 / 1024 if skew else 0, -90.8125, 0, -1 / 1024, 13.9375)
    west, south, east, north = zone_bounds
    configuration = tmp_path / "configuration"
    configuration.mkdir()
    (configuration / "ports.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"zone_id": "prq_muelles", "port_id": "prq"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [west, south],
                                    [east, south],
                                    [east, north],
                                    [west, north],
                                    [west, south],
                                ]
                            ],
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("PUERTOGT_CONFIG_DIR", str(configuration))
    for name in ("B04", "B03", "B02", "observable", "valid"):
        with rasterio.open(
            prepared / f"{name}.tif",
            "w",
            driver="GTiff",
            width=32,
            height=32,
            count=1,
            dtype="uint16",
            crs="EPSG:4326",
            transform=affine,
        ) as target:
            target.write(
                np.full((32, 32), 1 if name in {"observable", "valid"} else 1000, dtype="uint16"), 1
            )
    bundle = RasterBundle(
        band_paths={
            "B04": Path("data/pilot/prepared/B04.tif"),
            "B03": Path("pilot/prepared/B03.tif"),
            "B02": prepared / "B02.tif",
        },
        mask_paths={
            "observable": Path("data/pilot/prepared/observable.tif"),
            "valid": Path("pilot/prepared/valid.tif"),
        },
        crs="EPSG:4326",
        transform=tuple(affine)[:6],
        units={band: "DN_harmonized" for band in ("B04", "B03", "B02")},
        preprocessing_version="test-v1",
        sensor="sentinel-2",
        product_level="L1C",
    )
    scene = Scene(
        scene_id="prepared",
        acquisition_key="prepared",
        sensor="sentinel-2",
        product_level="L1C",
        captured_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        footprint={"type": "Point", "coordinates": [-90.79, 13.925]},
        port_ids=["prq"],
        metadata={"prepared_bundle": bundle.model_dump(mode="json")},
    )
    model = ModelManifest(
        model_id="test",
        version="v1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="test-v1",
    )
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    job_id = queue.enqueue(scene, "prq_muelles", processing_recipe(model), port_id="prq")
    working = tmp_path / "unrelated-working-directory"
    working.mkdir()
    monkeypatch.chdir(working)

    def predict(resolved, _model):
        for name, path in {**resolved.band_paths, **resolved.mask_paths}.items():
            with rasterio.open(path) as source:
                assert source.read(1)[0, 0] == (1 if name in {"observable", "valid"} else 1000)
        return []

    result = process_once(
        store,
        model,
        runtime=RuntimeLimits(reserve_bytes=0),
        predictor=predict,
    )

    assert result["status"] == expected_status
    assert result["published_detections"] == 0
    observation = store.get_observation(queue.get_job(job_id)["observation_id"])
    assert observation["detection_count"] == 0
    assert observation["observable_fraction"] == expected_fraction
    assert ("zone_partially_outside_crop" in observation["quality_flags"]) == (
        expected_status == "partial"
    )
    assert (store.data_dir / "assets" / result["observation_id"] / "observable.tif").is_file()


def test_worker_with_unknown_observability_never_publishes_zero_or_detections(tmp_path):
    store = Store(tmp_path / "volume" / "catalog.sqlite")
    bands = {}
    affine = from_origin(-90.79, 13.93, 0.001, 0.001)
    for name in ["B04", "B03", "B02"]:
        path = tmp_path / f"{name}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=8,
            height=8,
            count=1,
            dtype="uint16",
            crs="EPSG:4326",
            transform=affine,
        ) as target:
            target.write(np.full((8, 8), 1000, dtype="uint16"), 1)
        bands[name] = path
    bundle = RasterBundle(
        band_paths=bands,
        crs="EPSG:4326",
        transform=tuple(affine)[:6],
        units={name: "DN_harmonized" for name in bands},
        preprocessing_version="test-v1",
        sensor="sentinel-2",
        product_level="L1C",
    )
    scene = Scene(
        scene_id="s",
        acquisition_key="k",
        sensor="sentinel-2",
        product_level="L1C",
        captured_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        footprint={"type": "Point", "coordinates": [-90.785, 13.925]},
        port_ids=["prq"],
    )
    queue = JobQueue(store, runtime=RuntimeLimits(reserve_bytes=0))
    model = ModelManifest(
        model_id="test",
        version="v1",
        sensor="sentinel-2",
        task="detection",
        recipe_version="test-v1",
    )
    job_id = queue.enqueue(scene, "prq_muelles", processing_recipe(model), port_id="prq")
    predictions = [
        Prediction(
            prediction_id="p",
            geometry={"type": "Point", "coordinates": [-90.785, 13.925]},
            detection_score=0.99,
        )
    ]
    result = process_once(
        store,
        model,
        runtime=RuntimeLimits(reserve_bytes=0),
        prepare_fn=lambda *a, **k: bundle,
        predictor=lambda *a: predictions,
    )
    assert result["status"] == "unusable"
    assert result["published_detections"] is None
    observation = store.get_observation(queue.get_job(job_id)["observation_id"])
    assert observation["detection_count"] is None
    assert "observability_not_established" in observation["quality_flags"]
    proposal = list((store.data_dir / "assets").rglob("experimental-proposals.json"))[0]
    assert json.loads(proposal.read_text())["predictions"][0]["prediction_id"] == "p"
