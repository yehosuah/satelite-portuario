"""Compare base/adjusted fit on frozen TRAIN, without opening reserved labels."""
from __future__ import annotations

import argparse
import gc
import json
import resource
import sys
import time
from pathlib import Path

from train_phase2 import runtime_identity, source_hashes, write_json

from puertogt.cli import read_model
from puertogt.history import file_lock
from puertogt.ml.workflows import history_training_diagnostic_inputs, history_training_fit_report
from puertogt.store import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    if sha256_file(args.plan) != args.plan_sha256:
        raise ValueError("Explicit preregistered diagnostic plan checksum mismatch")
    plan = json.loads(args.plan.read_text())
    if plan.get("schema_version") != "s2-expanded-original-recipe-train-fit-plan-v1":
        raise ValueError("Unsupported TRAIN diagnostic plan")
    fit = plan["train_fit_diagnostic"]
    if fit["inference_floor"] != .1 or fit["fixed_reporting_thresholds"] != [.1, .8] or fit["iou"] != .5:
        raise ValueError("This bounded diagnostic fixes floor .10 and reports .10/.80 at IoU .5")
    experiment = args.experiment.resolve()
    freeze = json.loads((experiment / "freeze.json").read_text())
    completed = json.loads((experiment / "train.completed.json").read_text())
    if completed["status"] != "passed" or completed["freeze_sha256"] != sha256_file(experiment / "freeze.json"):
        raise ValueError("A completed matching training receipt is required")
    for relative, digest in completed["outputs"].items():
        if sha256_file(experiment / relative) != digest:
            raise ValueError("Completed TRAIN artifact changed")
    if source_hashes() != freeze["source_sha256"] or runtime_identity() != freeze["runtime"]:
        raise ValueError("Frozen inference/training source or runtime changed; replay its pinned revision")
    if sha256_file(experiment / "base-manifest.json") != freeze["base_manifest_sha256"]:
        raise ValueError("Frozen base model manifest changed")
    source = (ROOT / plan["train_source"]["path"]).resolve()
    if sha256_file(source / "manifest.json") != plan["train_source"]["sha256"]:
        raise ValueError("Preregistered TRAIN source changed")
    if (source != Path(freeze["train_source"])
            or freeze["datasets"]["train"]["sha256"] != plan["train_crops"]["sha256"]):
        raise ValueError("Experiment and predeclared TRAIN diagnostic refer to different data")
    if json.loads((source / "manifest.json").read_text()).get("split") != "train":
        raise ValueError("Only TRAIN is accepted by this diagnostic")
    output = experiment / "train-diagnostic"
    with file_lock(ROOT / "data/.heavy-worker.lock"):
        import torch

        torch.set_num_threads(4)
        torch.manual_seed(20261021)
        output.mkdir(exist_ok=False)
        started = time.monotonic()
        start = {"schema_version": "fixed-history-train-fit-start-v1",
                 "plan": {"path": str(args.plan.resolve()), "sha256": args.plan_sha256},
                 "freeze_sha256": sha256_file(experiment / "freeze.json"),
                 "script_sha256": sha256_file(Path(__file__)), "source_sha256": source_hashes(),
                 "dataset_sha256": plan["train_source"]["sha256"],
                 "thresholds": [.1, .8], "iou": .5, "device": "cpu", "threads": 4,
                 "calibration_or_test_labels_opened": False, "automatic_publication": False}
        write_json(output / "started.json", start)
        results = []
        try:
            for role, relative in [("base", "base-manifest.json"), ("adjusted", "trained/manifest.json")]:
                manifest_path = experiment / relative
                model = read_model(manifest_path, ROOT / "data")
                floor = model.model_copy(update={"publication_policy": {**model.publication_policy,
                                                                         "score_threshold": .1}})
                _, records = history_training_diagnostic_inputs(source, floor,
                                                                 cache_dir=output / role / "cache")
                reports = [history_training_fit_report(records, threshold=threshold) for threshold in [.1, .8]]
                report = {"role": role, "model_id": model.model_id, "weights_sha256": model.weights_sha256,
                          "model_manifest_sha256": sha256_file(manifest_path),
                          "reports_at_fixed_thresholds": reports, "threshold_selection_performed": False,
                          "scope": "training_fit_only_not_generalization", "automatic_publication": False}
                write_json(output / role / "predictions.json", records)
                write_json(output / role / "report.json", report)
                results.append({"role": role, "report_sha256": sha256_file(output / role / "report.json"),
                                "predictions_sha256": sha256_file(output / role / "predictions.json")})
                del records, reports, model, floor
                gc.collect()
            receipt = {**start, "status": "completed_training_diagnostic_only", "results": results,
                       "seconds": time.monotonic() - started,
                       "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                       * (1 if sys.platform == "darwin" else 1024),
                       "validation_eligible": False, "weights_or_labels_changed": False}
            write_json(output / "completed.json", receipt)
        except BaseException as exc:
            write_json(output / "failed.json", {"error_type": type(exc).__name__, "message": str(exc),
                                                "completed_results": results, "immutable_attempt": True})
            raise
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
