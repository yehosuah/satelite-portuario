"""Summarize frozen TRAIN order and recorded losses without loading a model."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
PROBE_SHA = "70ae139976e8594ca0d8e7294ac4124117c7350e1dcd220182cfff10401d4c09"
TRAINED_SHA = "c0e6c7620d0768c3eb0aee47901504f141296bd4c91b616bc83153dab1bbc7a0"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    if output.exists():
        raise FileExistsError(output)
    probe_path = HERE / "train-box-contract-probe-v2.json"
    trained_path = ROOT / "data/experiments/phase2-s2-round1-20261021/trained/manifest.json"
    if sha(probe_path) != PROBE_SHA or sha(trained_path) != TRAINED_SHA:
        raise ValueError("Pinned TRAIN input changed")
    probe = json.loads(probe_path.read_text())
    artifact = json.loads(trained_path.read_text())
    samples = probe["samples_in_training_order"]
    steps = artifact["metadata"]["training"]["steps"]
    if len(samples) != 40 or len(steps) != 120:
        raise ValueError("This summary is restricted to the registered January experiment")
    per_port = {}
    for port in ("prq", "pbr", "stc"):
        members = [sample for sample in samples if sample["port_id"] == port]
        rows = [row for row in probe["target_rows"] if row["port_id"] == port]
        physical = Counter(row["physical_key"] for row in rows)
        per_port[port] = {"crops": len(members), "positive_crops": sum(row["labels"] > 0 for row in members),
                          "label_instances": len(rows), "unique_physical_references": len(physical),
                          "physical_reference_occurrences": dict(physical)}
    epochs = []
    for epoch in range(3):
        current = steps[40 * epoch:40 * (epoch + 1)]
        for sample, step in zip(samples, current):
            if sample["labels"] != step["reference_boxes"]:
                raise ValueError("Recorded step order no longer matches the frozen sample order")
        positives = [step for step in current if step["reference_boxes"] > 0]
        negatives = [step for step in current if not step["reference_boxes"]]
        gt_only = [{"position_zero_based": index, "observation_id": samples[index]["observation_id"],
                    "references": step["reference_boxes"], "roi_supervised": step["supervision"]["roi_supervised"]}
                   for index, step in enumerate(current)
                   if 0 < step["reference_boxes"] < 128 and step["supervision"]["roi_supervised"] == step["reference_boxes"]]
        tail = current[26:]
        if any(step["reference_boxes"] for step in tail):
            raise ValueError("Expected the same terminal negative-only block")
        selected = current[1]
        epochs.append({"epoch": epoch + 1, "positive_updates": sum(not step.get("skipped") for step in positives),
                       "negative_updates": sum(not step.get("skipped") for step in negatives),
                       "skipped_steps": sum(bool(step.get("skipped")) for step in current),
                       "tail_14_negative_updates": sum(not step.get("skipped") for step in tail),
                       "tail_14_regression_losses_all_zero": all(
                           step.get("losses", {}).get(key, 0.0) == 0.0
                           for step in tail for key in ("detect_loss_rpn_box_reg", "detect_loss_box_reg")),
                       "roi_sample_contains_only_appended_gt_implied_by_count": gt_only,
                       "selected_crop": {"observation_id": samples[1]["observation_id"],
                                         "supervision": selected["supervision"], "losses": selected["losses"],
                                         "unmasked_rpn_positive_anchors": samples[1]["rpn_positive_anchors_including_low_quality_matches"]}})
    sources = ["src/puertogt/ml/workflows.py", "src/puertogt/ml/detection.py",
               ".venv/lib/python3.11/site-packages/rslearn/models/faster_rcnn.py",
               ".venv/lib/python3.11/site-packages/rslearn/train/data_module.py",
               ".venv/lib/python3.11/site-packages/torchvision/models/detection/rpn.py",
               ".venv/lib/python3.11/site-packages/torchvision/models/detection/roi_heads.py",
               ".venv/lib/python3.11/site-packages/torch/optim/adamw.py"]
    result = {"schema_version": "frozen-train-learning-signals-v1",
              "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "script_sha256": sha(Path(__file__)),
              "inputs": [{"path": str(probe_path.relative_to(ROOT)), "sha256": PROBE_SHA},
                         {"path": str(trained_path.relative_to(ROOT)), "sha256": TRAINED_SHA}],
              "sources": [{"path": path, "sha256": sha(ROOT / path)} for path in sources],
              "samples": len(samples), "positive_samples": sum(sample["labels"] > 0 for sample in samples),
              "negative_samples": sum(sample["labels"] == 0 for sample in samples),
              "per_port": per_port, "epochs": epochs,
              "sample_order": [{"position_zero_based": index, **sample} for index, sample in enumerate(samples)],
              "roi_inference_qualification": "RoI appends every GT; positive sampler cap is128 and every crop has<=10GT. When total selected RoIs equals GT count, no additional RPN proposal was selected. Counts greater than GT do not distinguish additional negatives from positives.",
              "limitations": {"weights_loaded": False, "new_model_forwards": 0, "optimizer_created": False,
                              "calibration_or_test_access": False, "geometry_or_label_changes": False,
                              "gradient_norms_measured": False, "causal_failure_reproduced": False,
                              "generalization_metrics_computed": False}}
    with output.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"output": str(output), "sha256": sha(output),
                      "positive_updates": sum(e["positive_updates"] for e in epochs),
                      "negative_updates": sum(e["negative_updates"] for e in epochs),
                      "gt_only_roi_steps": sum(len(e["roi_sample_contains_only_appended_gt_implied_by_count"]) for e in epochs)}, indent=2))


if __name__ == "__main__":
    main()
