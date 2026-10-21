"""Read-only TRAIN probe; no image inference, optimizer, DB or held-out label access."""
from __future__ import annotations

import ast
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import torch
from affine import Affine
from pyproj import Transformer
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform
from torchvision.ops import box_iou

from puertogt.ml.workflows import labels_for_sample, pixel_boxes
from puertogt.review import bundle_from_evidence
from rslearn.models.faster_rcnn import FasterRCNN, NoopTransform

warnings.filterwarnings("ignore", category=DeprecationWarning)
torch.set_num_threads(1)
ROOT = Path(__file__).resolve().parents[4]
TRAIN = ROOT / "data/datasets/phase2-reviews/s2-january-v1/train-crops"
PARENT = ROOT / "data/datasets/phase2-reviews/s2-january-v1/source"
EXPERIMENT = ROOT / "data/experiments/phase2-s2-round1-20261021"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True, help="New immutable JSON result path")
OUTPUT = parser.parse_args().output.resolve()
if OUTPUT.exists():
    raise FileExistsError("Choose a new output path; previous diagnostic results are immutable")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quantiles(values):
    return dict(zip(("min", "p25", "median", "p75", "max"), map(float, np.quantile(values, [0, .25, .5, .75, 1])))) if values else None


def physical(feature):
    return feature["properties"]["provenance"]["unique_physical_object_key"]


def polygon_vertices(geom):
    if geom.geom_type == "Polygon":
        return list(geom.exterior.coords)
    return [point for g in geom.geoms for point in polygon_vertices(g)]


manifest_path = TRAIN / "manifest.json"
manifest = json.loads(manifest_path.read_text())
assert manifest["split"] == "train"
assert digest(manifest_path) == "f0334c9d341f57624794d370a44091308a8f08ae31624f69a7c057ad4e0f8686"
parent = json.loads((PARENT / "manifest.json").read_text())
assert parent["split"] == "train" and digest(PARENT / "manifest.json") == manifest["parent_manifest_sha256"]
parent_features = {}
for sample in parent["samples"]:
    parent_features[sample["observation_id"]] = {physical(f): f for f in labels_for_sample(PARENT, sample)}

# Execute only the exact pinned process_inputs method. Importing DetectionTask would
# require unused scipy/torchmetrics dependencies, which this task must not install.
rslearn_dir = Path(importlib.util.find_spec("rslearn").origin).parent
upstream_path = rslearn_dir / "train/tasks/detection.py"
module = ast.parse(upstream_path.read_text())
cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "DetectionTask")
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "process_inputs")
probe_module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method], type_ignores=[])
namespace = {"np": np, "torch": torch}
exec(compile(ast.fix_missing_locations(probe_module), str(upstream_path), "exec"), namespace)
upstream_process = namespace["process_inputs"]
task = SimpleNamespace(box_size=15, filters=[], property_name="category", read_class_id=False,
                       classes=["unknown", "vessel"], skip_unknown_categories=False,
                       exclude_by_center=True, clip_boxes=True, skip_empty_examples=False)
head = FasterRCNN(downsample_factors=[4, 8, 16, 32], num_channels=128, num_classes=2,
                  anchor_sizes=[[32], [64], [128], [256]])
head.eval()  # No weights loaded, no model forward, no optimizer created.
anchor_cache = {}
rows, samples, all_target_ious, all_anchor_ious = [], [], [], []
unique_by_port, occurrences = defaultdict(set), Counter()
max_conversion_error = max_parent_clip_error = 0.0
for sample in manifest["samples"]:
    bundle = bundle_from_evidence(sample, TRAIN)
    width, height = sample["crop_window"][2:]
    height, width = int(height), int(width)
    affine = Affine(*bundle.transform)
    assert affine.b == 0 and affine.d == 0, "This claim is limited to north-up TRAIN grids"
    features = labels_for_sample(TRAIN, sample)
    targets = pixel_boxes(features, bundle, height, width)
    projected = Transformer.from_crs("EPSG:4326", bundle.crs, always_xy=True)
    footprint = Polygon([affine * point for point in [(0, 0), (width, 0), (width, height), (0, height)]])
    pad_h, pad_w = math.ceil(height / 32) * 32, math.ceil(width / 32) * 32
    if (pad_h, pad_w) not in anchor_cache:
        gen = head.rpn.anchor_generator
        gen.set_cell_anchors(torch.float32, torch.device("cpu"))
        grids = [(pad_h // s, pad_w // s) for s in [4, 8, 16, 32]]
        strides = [[torch.tensor(s), torch.tensor(s)] for s in [4, 8, 16, 32]]
        anchor_cache[(pad_h, pad_w)] = torch.cat(gen.grid_anchors(grids, strides))
    anchors = anchor_cache[(pad_h, pad_w)]
    t = torch.from_numpy(targets)
    max_iou = box_iou(t, anchors).max(1).values if len(t) else torch.zeros(0)
    natural_positive_counts = (box_iou(t, anchors) >= .7).sum(1) if len(t) else torch.zeros(0)
    forced_labels, _ = head.rpn.assign_targets_to_anchors([anchors], [{"boxes": t}])
    for i, (feature, target) in enumerate(zip(features, targets)):
        geometry = transform(projected.transform, shape(feature["geometry"]))
        exact_pixel_geometry = transform(lambda x, y: (~affine) * (x, y), geometry)
        x0, y0, x1, y1 = exact_pixel_geometry.bounds
        expected = np.array([max(0, x0), max(0, y0), min(width, x1), min(height, y1)])
        error = float(np.max(np.abs(expected - target)))
        max_conversion_error = max(max_conversion_error, error)
        assert error < 5e-5
        parent_feature = parent_features[sample["parent_observation_id"]][physical(feature)]
        expected_geometry = transform(projected.transform, shape(parent_feature["geometry"])).intersection(footprint)
        clip_error = geometry.symmetric_difference(expected_geometry).area
        max_parent_clip_error = max(max_parent_clip_error, clip_error)
        assert clip_error < 1e-3, (sample["observation_id"], clip_error)
        upstream_feature = SimpleNamespace(properties={"category": "vessel"}, geometry=SimpleNamespace(shp=exact_pixel_geometry))
        _, upstream_target = upstream_process(task, {"targets": [upstream_feature]}, SimpleNamespace(crop_bounds=(0, 0, width, height)), True)
        fixed = upstream_target["boxes"]
        assert len(fixed) in (0, 1)
        contract_iou = float(box_iou(t[i:i+1], fixed)[0, 0]) if len(fixed) else None
        if contract_iou is not None:
            all_target_ious.append(contract_iou)
        all_anchor_ious.append(float(max_iou[i]))
        key = physical(feature)
        unique_by_port[sample["port_id"]].add(key)
        occurrences[key] += 1
        rows.append({"observation_id": sample["observation_id"], "port_id": sample["port_id"],
                     "physical_key": key, "pixel_box": target.tolist(), "upstream_fixed_target": fixed[0].tolist() if len(fixed) else None,
                     "upstream_excluded_integer_center_at_crop_boundary": not bool(len(fixed)),
                     "width_px": float(target[2] - target[0]), "height_px": float(target[3] - target[1]),
                     "visual_vs_upstream_target_iou": contract_iou,
                     "best_configured_anchor_iou": float(max_iou[i]),
                     "anchors_at_fg_iou_point7": int(natural_positive_counts[i]),
                     "pixel_conversion_max_error_px": error,
                     "parent_crop_intersection_error_m2": float(clip_error),
                     "clipped_to_training_crop": feature["properties"].get("clipped_to_training_crop", False)})
    samples.append({"observation_id": sample["observation_id"], "port_id": sample["port_id"],
                    "acquisition_key": sample["acquisition_key"], "labels": len(features),
                    "reviewed_pixels": sample.get("reviewed_pixel_count"),
                    "rpn_positive_anchors_including_low_quality_matches": int((forced_labels[0] == 1).sum())})
# Native-grid padding check using the actual rslearn transform, no backbone.
image = torch.zeros((9, 447, 449))
example_target = [{"boxes": torch.tensor([[100., 100., 120., 108.]]), "labels": torch.ones(1, dtype=torch.long)}]
transformed, untouched = NoopTransform()([image], example_target)
assert torch.equal(untouched[0]["boxes"], example_target[0]["boxes"])
assert transformed.tensors.shape[-2:] == (448, 480)

trained_path = EXPERIMENT / "trained/manifest.json"
training = json.loads(trained_path.read_text())["metadata"]["training"]
assert training["dataset_manifest_sha256"] == digest(manifest_path)
assert len(training["steps"]) == len(samples) * training["epochs"]
steps_summary = []
for epoch in range(training["epochs"]):
    steps = training["steps"][epoch * len(samples):(epoch + 1) * len(samples)]
    losses = defaultdict(list)
    for step in steps:
        for key, value in step.get("losses", {}).items():
            losses[key].append(value)
    steps_summary.append({"epoch": epoch + 1, "steps": len(steps), "updates": sum(bool(s.get("updated_parameters")) for s in steps),
                          "skipped": sum(bool(s.get("skipped")) for s in steps),
                          "training_loss_quantiles": {k: quantiles(v) for k, v in losses.items()}})
source_paths = [ROOT / "src/puertogt/ml/workflows.py", ROOT / "src/puertogt/ml/detection.py",
                ROOT / "src/puertogt/ml/dataset_crops.py", ROOT / "scripts/train_phase2.py",
                ROOT / "tmp/pilot/rslearn_projects/data/sentinel2_vessels/config.yaml",
                upstream_path, rslearn_dir / "models/faster_rcnn.py"]
result = {"schema_version": "train-box-contract-readonly-probe-v1", "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
          "train_manifest_sha256": digest(manifest_path), "parent_manifest_sha256": digest(PARENT / "manifest.json"),
          "trained_manifest_sha256": digest(trained_path), "script_sha256": digest(__file__),
          "sources": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in source_paths],
          "source_pins": {"rslearn_projects": "0bae49abb76d1deee9b6b6ca2a59212611ebc6fe", "rslearn": "e0ec9c8f404f348de1b7d64521f7afc588c80e0a"},
          "packages": {p: importlib.metadata.version(p) for p in ["torch", "torchvision", "rslearn", "numpy", "shapely", "pyproj"]},
          "limits": {"train_only": True, "calibration_or_test_labels_opened": False, "model_weights_loaded": False,
                     "model_forward_executed": False, "optimizer_created": False, "source_or_dataset_edits": False,
                     "diagnostic_not_validation": True, "upstream_method_isolated_from_exact_AST_due_to_unused_missing_scipy": True},
          "checks": {"actual_pixel_box_conversion_passed": True, "max_pixel_conversion_error_px": max_conversion_error,
                     "parent_to_crop_clipping_passed": True, "max_parent_crop_symmetric_difference_m2": max_parent_clip_error,
                     "noop_padding_preserves_targets": True, "calibration_failure_reproduced": False,
                     "upstream_and_local_target_semantics_equal": False},
          "summary": {"samples": len(samples), "positive_sample_count": sum(s['labels'] > 0 for s in samples),
                      "crop_label_instances": len(rows), "unique_physical_references": len(occurrences),
                      "unique_references_by_port": {p: len(keys) for p, keys in unique_by_port.items()},
                      "object_occurrences_per_epoch": quantiles(list(occurrences.values())),
                      "target_width_px": quantiles([r['width_px'] for r in rows]),
                      "target_height_px": quantiles([r['height_px'] for r in rows]),
                      "visual_vs_upstream_target_iou": quantiles(all_target_ious),
                      "best_configured_anchor_iou": quantiles(all_anchor_ious),
                      "targets_with_natural_anchor_at_point7": sum(r['anchors_at_fg_iou_point7'] > 0 for r in rows),
                      "targets_with_visual_vs_upstream_iou_at_least_point5": sum(v >= .5 for v in all_target_ious),
                      "upstream_integer_boundary_center_exclusions": sum(r['upstream_excluded_integer_center_at_crop_boundary'] for r in rows),
                      "strictly_training_geometry_statistics_not_performance_metrics": True},
          "samples_in_training_order": samples, "target_rows": rows, "recorded_train_steps": steps_summary}
with OUTPUT.open("x") as handle:
    json.dump(result, handle, indent=2, allow_nan=False)
    handle.write("\n")
print(json.dumps({"checks": result['checks'], "summary": result['summary'], "output": str(OUTPUT)}, indent=2))
