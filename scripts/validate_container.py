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

    from puertogt.cli import read_model
    from puertogt.contracts import RasterBundle
    from puertogt.ml.detection import detect, load_detector
    from puertogt.preprocess import read_model_input
    from puertogt.worker import resolve_bundle_paths

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
    data_dir = Path(os.environ.get("PUERTOGT_DATA_DIR", "/data"))
    report["data_directory"] = str(data_dir)
    report["path_resolution"] = "CLI read_model and worker resolve_bundle_paths"
    report["inference"] = []
    for sensor, recipe, bundle_name in (
        ("sentinel-1", "s1.yaml", "prq-s1-prepared"),
        ("sentinel-2", "s2.yaml", "prq-prepared"),
    ):
        model = read_model(config_path() / "models" / recipe, data_dir)
        path = data_dir / "pilot" / bundle_name / "bundle.json"
        bundle = resolve_bundle_paths(
            RasterBundle.model_validate_json(path.read_text()), data_dir, relative_to=path.parent
        )
        input_paths = sorted(set((*bundle.band_paths.values(), *bundle.mask_paths.values())))
        assert Path(model.weights_path).is_relative_to(data_dir)
        assert all(Path(item).is_relative_to(data_dir) for item in input_paths)
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
                "resolved_weights_path": model.weights_path,
                "model_manifest_sha256": sha256_file(config_path() / "models" / recipe),
                "bundle_sha256": sha256_file(path),
                "input_files_sha256": {
                    str(Path(item).relative_to(data_dir)): sha256_file(item) for item in input_paths
                },
                "input_shape": list(input_array.shape),
                "model_load_seconds": load_seconds,
                "inference_seconds": elapsed,
                "detections": len(predictions),
                "scores": [prediction.detection_score for prediction in predictions],
                "peak_process_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
                "scope": "CPU_runtime_operability_only_not_local_accuracy",
            }
        )
        del detector, input_array, predictions
        gc.collect()
package_root = Path(puertogt.__file__).parent
report["module_sha256"] = {
    str(path.relative_to(package_root)): sha256_file(path)
    for path in sorted(package_root.rglob("*.py"))
}
print("VALIDATION_JSON=" + json.dumps(report, sort_keys=True))
