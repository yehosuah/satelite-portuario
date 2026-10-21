import json
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.transform import from_origin

from puertogt.contracts import ModelManifest, Prediction, RasterBundle, Scene
from puertogt.jobs import JobQueue, RuntimeLimits
from puertogt.models import processing_recipe
from puertogt.store import Store
from puertogt.worker import process_once


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
