"""Offline runtime smoke for the installed Docker package; requires pilot data for ML.

The API check creates only a temporary database. ML uses the two real public
checkpoints and prepared pilot bundles, and does not establish local accuracy.
"""

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

import puertogt
from puertogt.api import config_path, create_app
from puertogt.store import Store, sha256_file

parser = argparse.ArgumentParser()
parser.add_argument("--api-only", action="store_true")
args = parser.parse_args()
report = {
    "platform": platform.platform(),
    "machine": platform.machine(),
    "python": platform.python_version(),
    "uid": os.getuid(),
    "installed_package": str(Path(puertogt.__file__).resolve()),
    "configuration_directory": str(config_path().resolve()),
    "versions": {
        name: importlib.metadata.version(name)
        for name in ("satelite-portuario", "rasterio", "fastapi")
    },
}
assert "/.venv/lib/" in report["installed_package"], (
    "Package must be installed, not imported from src"
)
assert "/.venv/lib/" in report["configuration_directory"], "Default config must ship in the package"
with tempfile.TemporaryDirectory(prefix="puertogt-docker-") as root:
    store = Store(Path(root) / "catalog.sqlite")
    asset_path = Path(root) / "assets" / "smoke.bin"
    asset_path.parent.mkdir()
    asset_path.write_bytes(b"registered evidence for container smoke")
    asset_id = store.register_asset(
        "assets/smoke.bin", metadata={"attribution": "synthetic service smoke"}
    )
    with TestClient(create_app(store)) as client:
        health = client.get("/health")
        ports = client.get("/v1/ports")
        ui = client.get("/")
        asset = client.get(f"/v1/assets/{asset_id}")
        assert health.status_code == ports.status_code == ui.status_code == asset.status_code == 200
        assert asset.content == asset_path.read_bytes()
        assert "text/html" in ui.headers["content-type"]
        features = ports.json().get("features", [])
        assert len(features) == 12
        report["service_smoke"] = {
            "health": health.json(),
            "ports_features": len(features),
            "ui_status": ui.status_code,
            "registered_asset_status": asset.status_code,
            "registered_asset_sha256": hashlib.sha256(asset.content).hexdigest(),
        }
if not args.api_only:
    import fiona
    import rasterio
    import torch

    from puertogt.contracts import RasterBundle
    from puertogt.ml.detection import detect, load_detector, load_manifest
    from puertogt.preprocess import read_model_input

    torch.set_num_threads(4)
    report["versions"].update(
        {
            name: importlib.metadata.version(name)
            for name in ("torch", "torchvision", "rslearn", "fiona", "satlaspretrain-models")
        }
    )
    report["device"] = "cpu"
    report["gdal_versions"] = {
        "fiona": fiona.__gdal_version__,
        "rasterio": rasterio.__gdal_version__,
    }
    report["torch_threads"] = torch.get_num_threads()
    report["inference"] = []
    for sensor, recipe, bundle_name in (
        ("sentinel-1", "s1.yaml", "prq-s1-prepared"),
        ("sentinel-2", "s2.yaml", "prq-prepared"),
    ):
        model = load_manifest(config_path() / "models" / recipe)
        path = Path("data/pilot") / bundle_name / "bundle.json"
        bundle = RasterBundle.model_validate_json(path.read_text())
        start = time.monotonic()
        detector = load_detector(model, device="cpu")
        load_seconds = time.monotonic() - start
        input_array = read_model_input(bundle, model)
        start = time.monotonic()
        predictions = detect(bundle, model, detector=detector, device="cpu")
        elapsed = time.monotonic() - start
        report["inference"].append(
            {
                "sensor": sensor,
                "model_id": model.model_id,
                "weights_sha256": model.weights_sha256,
                "model_manifest_sha256": sha256_file(config_path() / "models" / recipe),
                "bundle_sha256": sha256_file(path),
                "input_shape": list(input_array.shape),
                "model_load_seconds": load_seconds,
                "inference_seconds": elapsed,
                "detections": len(predictions),
                "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "scope": "CPU_runtime_operability_only_not_local_accuracy",
            }
        )
        del detector, input_array, predictions
        gc.collect()
report["module_sha256"] = {
    name: sha256_file(Path(puertogt.__file__).parent / name)
    for name in ("api.py", "store.py", "ml/detection.py", "preprocess.py")
}
print("VALIDATION_JSON=" + json.dumps(report, sort_keys=True))
