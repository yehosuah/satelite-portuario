"""Exercise the real training/export/reload loop with one public training crop.

This is deliberately not a local test set and cannot establish local accuracy.
Run pilot_inference.py public-sample first. The outputs stay under ignored data/.
"""

import argparse
import json
import shutil
from pathlib import Path

import torch
from pyproj import Transformer
from rasterio import Affine

from puertogt.cli import execute, parser, read_model
from puertogt.contracts import RasterBundle
from puertogt.ml.detection import detect
from puertogt.store import json_text, sha256_file


def main():
    parser_args = argparse.ArgumentParser()
    parser_args.add_argument("--sensor", choices=["s1", "s2"], default="s2")
    parser_args.add_argument("--version", default="v1")
    args = parser_args.parse_args()
    torch.set_num_threads(4)
    source = Path(
        "data/pilot/upstream-prepared" if args.sensor == "s2" else "data/pilot/s1-upstream-prepared"
    )
    bundle = RasterBundle.model_validate_json((source / "bundle.json").read_text())
    boxes = json.loads((source / "reference_boxes.json").read_text())
    experiment_name = "public-smoke-s2" if args.sensor == "s2" else "public-negative-smoke-s1"
    root = Path(f"data/datasets/{experiment_name}-train-{args.version}")
    root.mkdir(parents=True, exist_ok=False)
    affine = Affine(*bundle.transform)
    project = Transformer.from_crs(bundle.crs, "EPSG:4326", always_xy=True)
    features = []
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        corners = [
            [*project.transform(*(affine * (x, y)))]
            for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))
        ]
        features.append(
            {
                "type": "Feature",
                "properties": {"id": f"upstream-{index}"},
                "geometry": {"type": "Polygon", "coordinates": [corners]},
            }
        )
    labels = root / "labels.geojson"
    labels.write_text(json_text({"type": "FeatureCollection", "features": features}))
    evidence = []
    for name, path in {**bundle.band_paths, **bundle.mask_paths}.items():
        target = root / f"{name}.tif"
        shutil.copyfile(path, target)
        evidence.append(
            {
                "path": target.name,
                "sha256": sha256_file(target),
                "metadata": {
                    "crs": bundle.crs,
                    "bundle": {
                        "bands": [name] if name in bundle.band_paths else [],
                        "masks": [name] if name in bundle.mask_paths else [],
                        "band_indices": {},
                        "units": bundle.units,
                        "transform": bundle.transform,
                        "sensor": bundle.sensor,
                        "product_level": bundle.product_level,
                        "preprocessing_version": bundle.preprocessing_version,
                        "metadata": bundle.metadata,
                    },
                },
            }
        )
    manifest = {
        "version": f"{experiment_name}-train-{args.version}",
        "split": "train",
        "samples": [
            {
                "observation_id": experiment_name,
                "captured_at": bundle.metadata["captured_at"],
                "split_groups": [experiment_name],
                "labels": labels.name,
                "sha256": sha256_file(labels),
                "evidence_assets": evidence,
                "reference_provenance": {
                    "kind": "public_dataset",
                    "reference": bundle.metadata.get(
                        "reference_source", "Ai2 public radar training sample"
                    ),
                    "license": "CC-BY-4.0",
                },
            }
        ],
    }
    (root / "manifest.json").write_text(json_text(manifest))
    destination = Path(f"data/trained/{experiment_name}-{args.version}")
    arguments = parser().parse_args(
        [
            "train-detector",
            str(root),
            str(destination),
            "--model",
            f"config/models/{args.sensor}.yaml",
            "--version",
            f"{experiment_name}-{args.version}",
        ]
    )
    result = execute(arguments)
    trained = read_model(destination / "manifest.json", Path("data"))
    predictions = detect(bundle, trained)
    report = {
        **result,
        "manifest": str(destination / "manifest.json"),
        "reloaded_checkpoint_inference_count": len(predictions),
        "reference_boxes": len(boxes),
        "training": trained.metadata["training"],
        "scope": "one public training crop; operability only; no local training or held-out accuracy claim",
    }
    report_name = "training-roundtrip" if args.sensor == "s2" else "radar-training-roundtrip"
    Path(f"reports/pilot/{report_name}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
