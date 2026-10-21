"""Import measured pilot crops, process overlapping zones and check the real local API."""

import argparse
import json
import time
from pathlib import Path

import rasterio
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely.geometry import box, shape

from puertogt.api import create_app
from puertogt.catalog import load_ports
from puertogt.cli import read_model, runtime_limits
from puertogt.contracts import RasterBundle, Scene
from puertogt.jobs import JobQueue
from puertogt.models import processing_recipe
from puertogt.store import Store
from puertogt.worker import process_once


def main():
    import torch

    options = argparse.ArgumentParser()
    options.add_argument("--sensor", choices=["s1", "s2"], default="s2")
    args = options.parse_args()
    torch.set_num_threads(4)
    store = Store(Path("data/catalog.sqlite"), Path("data"))
    model = read_model(Path(f"config/models/{args.sensor}.yaml"), store.data_dir)
    ports = load_ports(Path("config/ports.geojson"))
    jobs = []
    for port in ("prq", "stc", "pbr"):
        base = Path(
            f"data/pilot/{port}-prepared"
            if args.sensor == "s2"
            else f"data/pilot/{port}-s1-prepared"
        )
        scene_path = base / "scene.json"
        if not scene_path.exists() and port == "prq":
            scene_path = Path("data/pilot/prq-20260910/scene.json")
        scene = Scene.model_validate_json(scene_path.read_text())
        bundle = RasterBundle.model_validate_json((base / "bundle.json").read_text())
        scene.metadata["prepared_bundle"] = bundle.model_dump(mode="json")
        with rasterio.open(next(iter(bundle.band_paths.values()))) as raster:
            project = Transformer.from_crs(raster.crs, "EPSG:4326", always_xy=True)
            bounds = raster.bounds
            extent = box(
                *project.transform(bounds.left, bounds.bottom),
                *project.transform(bounds.right, bounds.top),
            )
        queue = JobQueue(store, runtime=runtime_limits())
        for zone in ports[port]["zones"]:
            if not shape(zone["geometry"]).intersects(extent):
                continue
            jobs.append(
                queue.enqueue(
                    scene, zone["properties"]["zone_id"], processing_recipe(model), port_id=port
                )
            )
    started, results = time.monotonic(), []
    for _ in jobs:
        result = process_once(store, model, runtime=runtime_limits())
        if result is None:
            break
        results.append(result)
    client = TestClient(create_app(store))
    checks = {}
    for path in (
        "/health",
        "/v1/ports",
        "/v1/observations",
        "/v1/detections",
        "/v1/coverage",
        "/v1/models",
        "/v1/jobs",
        "/",
    ):
        response = client.get(path)
        checks[path] = response.status_code
        response.raise_for_status()
    report = {
        "scope": "operational_integration_only; not an accuracy evaluation",
        "sensor": model.sensor,
        "ports": ["prq", "stc", "pbr"],
        "unique_jobs": len(set(jobs)),
        "processed": results,
        "seconds": time.monotonic() - started,
        "http_status": checks,
    }
    name = "service-validation" if args.sensor == "s2" else "radar-service-validation"
    Path(f"reports/pilot/{name}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
