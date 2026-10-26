"""Measure TRAIN target/anchor geometry; this does not execute a detector."""

import json
import math
import statistics
from pathlib import Path
from types import SimpleNamespace

from puertogt.ml.workflows import labels_for_sample, pixel_boxes
from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "data/datasets/phase2-reviews/s2-january-february-v1/train-crops"
EXPECTED = "c5a9b7e542d6a61b09ab5b926aa9cb9f10ee5cb1ac0de172144155b2a5099adc"


def best_centered_iou(width, height, sizes):
    values = []
    for size in sizes:
        for ratio in (.5, 1., 2.):
            # Match torchvision's rounded base anchors at a perfectly aligned
            # center. Real grid alignment can only reduce the best IoU.
            aw = 2 * round(size / math.sqrt(ratio) / 2)
            ah = 2 * round(size * math.sqrt(ratio) / 2)
            intersection = min(width, aw) * min(height, ah)
            values.append(intersection / (width * height + aw * ah - intersection))
    return max(values)


def run():
    manifest = DATASET / "manifest.json"
    assert sha256_file(manifest) == EXPECTED
    data = json.loads(manifest.read_text())
    assert data["split"] == "train"
    rows = []
    for sample in data["samples"]:
        metadata = next(asset["metadata"] for asset in sample["evidence_assets"]
                        if asset["metadata"].get("bundle"))
        descriptor = metadata["bundle"]
        bundle = SimpleNamespace(transform=descriptor["transform"], crs=metadata["crs"])
        features = labels_for_sample(DATASET, sample)
        _, _, width, height = sample["crop_window"]
        boxes = pixel_boxes(features, bundle, height, width)
        for feature, (x1, y1, x2, y2) in zip(features, boxes):
            w, h = float(x2-x1), float(y2-y1)
            rows.append({"port_id": sample["port_id"], "observation_id": sample["observation_id"],
                         "object_key": feature["properties"]["provenance"]["unique_physical_object_key"],
                         "width_px": w, "height_px": h,
                         "baseline_best_centered_anchor_iou": best_centered_iou(w, h, (32, 64, 128, 256)),
                         "hypothesis_best_centered_anchor_iou": best_centered_iou(w, h, (8, 16, 32, 64))})
    summary = {}
    for name in ("baseline", "hypothesis"):
        values = [row[name + "_best_centered_anchor_iou"] for row in rows]
        summary[name] = {"median_best_centered_iou": statistics.median(values),
                         "instances_at_least_0_5": sum(v >= .5 for v in values),
                         "instances_at_least_0_7": sum(v >= .7 for v in values)}
    value = {"created_at": utc_text(), "script_sha256": sha256_file(Path(__file__)),
             "train_manifest_sha256": EXPECTED, "train_dataset": str(DATASET.relative_to(ROOT)),
             "crop_label_instances": len(rows), "unique_objects": len({r["object_key"] for r in rows}),
             "baseline_anchor_sizes": [32, 64, 128, 256], "hypothesis_anchor_sizes": [8, 16, 32, 64],
             "aspect_ratios": [.5, 1., 2.], "summary": summary, "rows": rows,
             "interpretation": "Ideal centered anchor overlap on existing TRAIN labels only; cropped occurrences are dependent. This tests geometric compatibility, not the performance of a new detector.",
             "limits": ["No weights loaded; no forward pass, optimizer, CAL or TEST input.",
                        "No alternate recipe or threshold selected; smaller anchors are only a hypothesis.",
                        "RPN low-quality matching can train even when no anchor reaches IoU0.7.",
                        "No guarantee of precision improvement or adequate Caribbean support."],
             "local_validation_demonstrated": False}
    output = Path(__file__).parent / "train-anchor-geometry.json"
    with output.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"instances": len(rows), "unique_objects": value["unique_objects"], "summary": summary}, indent=2))


if __name__ == "__main__":
    run()
