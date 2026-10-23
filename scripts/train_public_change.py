"""Run the predeclared public change experiment under the shared heavy lock.

This is research on a fixed public pixel-coordinate subset. It does not register
models, modify observations, publish weights, tune on test, or certify local use.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import platform
import random
import resource
import shutil
import signal
import time
from pathlib import Path

import yaml

from puertogt.contracts import ModelManifest
from puertogt.ml.change_evaluation import evaluate_change_dataset
from puertogt.ml.change_workflows import train_change_dataset
from puertogt.store import json_text, sha256_file, utc_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("reports/change/public-data/oscd-bounded-protocol-v3.json"),
    )
    parser.add_argument("--dataset", type=Path, default=Path("data/datasets/oscd-bounded-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text())
    for split in ("train", "test"):
        if (
            sha256_file(args.dataset / split / "manifest.json")
            != protocol["dataset"][split + "_manifest_sha256"]
        ):
            raise ValueError("Frozen dataset manifest changed")
    if args.output.exists():
        raise FileExistsError("Experiments are immutable; use a new output directory")
    if shutil.disk_usage(args.data_dir).free < protocol["reserve_free_bytes"]:
        raise OSError("Disk reserve not met")
    with (args.data_dir / ".heavy-worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        args.output.mkdir(parents=True)
        started = time.monotonic()
        report = {
            "schema": "public-change-experiment-v1",
            "started_at": utc_text(),
            "protocol_sha256": sha256_file(args.protocol),
            "status": "running",
            "scope": "noncommercial public subset research; no local validation",
            "train_manifest_sha256": protocol["dataset"]["train_manifest_sha256"],
            "test_manifest_sha256": protocol["dataset"]["test_manifest_sha256"],
        }
        (args.output / "run.json").write_text(json_text(report))
        try:
            sources = [
                Path(f"src/puertogt/{name}")
                for name in (
                    "ml/oscd.py",
                    "ml/change.py",
                    "ml/change_workflows.py",
                    "ml/change_evaluation.py",
                    "ml/training.py",
                    "preprocess.py",
                    "contracts.py",
                    "store.py",
                )
            ]
            sources += [Path(__file__).resolve(), Path("config/models/change.yaml"), args.protocol]
            source_hashes = {}
            for source in sources:
                relative = source.resolve().relative_to(Path.cwd())
                destination = args.output / "source" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                source_hashes[str(relative)] = sha256_file(source)
            report["source_sha256"] = source_hashes
            os.environ["OMP_NUM_THREADS"] = str(protocol["cpu_threads"])
            os.environ["MKL_NUM_THREADS"] = str(protocol["cpu_threads"])
            import numpy as np
            import torch

            torch.set_num_threads(protocol["cpu_threads"])
            torch.set_num_interop_threads(1)
            seed = protocol["training"]["seed"]
            random.seed(seed)
            np.random.seed(seed)
            torch.manual_seed(seed)
            torch.use_deterministic_algorithms(True)

            def deadline(*_):
                raise TimeoutError("Public change experiment reached its predeclared heavy budget")

            old_handler = signal.signal(signal.SIGALRM, deadline)
            signal.alarm(protocol["heavy_budget_seconds"])
            base = ModelManifest.model_validate(
                yaml.safe_load(Path("config/models/change.yaml").read_text())
            )
            base.metadata["encoder_checkpoint"] = str(
                Path(base.metadata["encoder_checkpoint"]).resolve()
            )
            base.metadata.update(
                {
                    "decoder_channels": protocol["training"]["decoder_channels"],
                    "public_training_scope": "OSCD bounded source-pixel subset; no local validation",
                    "label_license": protocol["licenses"],
                    "experiment_protocol_sha256": report["protocol_sha256"],
                }
            )
            base.publication_policy.update(
                {
                    "change_threshold": protocol["training"]["evaluation_threshold"],
                    "min_area_m2": protocol["training"]["minimum_area_m2"],
                    "max_alignment_rmse_pixels": protocol["training"]["max_alignment_rmse_pixels"],
                    "minimum_observable_fraction": protocol["training"][
                        "minimum_observable_fraction"
                    ],
                }
            )
            print(json_text({"stage": "training_started", "time": utc_text()}), flush=True)
            model = train_change_dataset(
                args.dataset / "train",
                base,
                args.output / "model",
                epochs=protocol["training"]["epochs"],
                learning_rate=protocol["training"]["learning_rate"],
                positive_weight=protocol["training"]["class_weight"],
                max_seconds=protocol["heavy_budget_seconds"] - 60,
                reserved_manifests=[args.dataset / "test" / "manifest.json"],
            )
            print(
                json_text(
                    {
                        "stage": "weights_frozen",
                        "sha256": model.weights_sha256,
                        "steps": model.metadata["training"]["steps"],
                        "seconds": time.monotonic() - started,
                    }
                ),
                flush=True,
            )
            # Hash and export before the first test forward. No subsequent optimization.
            report["trained_manifest_sha256"] = sha256_file(args.output / "model" / "manifest.json")
            report["weights_sha256"] = model.weights_sha256
            (args.output / "before-test.json").write_text(json_text(report))
            loaded = model.model_copy(
                update={"weights_path": str((args.output / "model" / model.weights_path).resolve())}
            )
            evaluation = evaluate_change_dataset(args.dataset / "test", loaded)
            (args.output / "test-evaluation.json").write_text(json_text(evaluation))
            report.update(
                {
                    "status": "completed",
                    "evaluation": evaluation["total"],
                    "model_id": model.model_id,
                    "training": model.metadata["training"],
                    "environment": {
                        "python": platform.python_version(),
                        "torch": str(torch.__version__),
                        "numpy": np.__version__,
                        "cpu_threads": torch.get_num_threads(),
                    },
                    "local_validation": False,
                    "automatic_publication": False,
                }
            )
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
        except BaseException as error:
            report.update(
                {"status": "failed", "error_type": type(error).__name__, "error": str(error)}
            )
            raise
        finally:
            signal.alarm(0)
            report["seconds"] = time.monotonic() - started
            report["finished_at"] = utc_text()
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report["peak_rss_bytes"] = peak if platform.system() == "Darwin" else peak * 1024
            (args.output / "run.json").write_text(json_text(report))
            print(
                json_text({key: report[key] for key in ("status", "seconds", "peak_rss_bytes")}),
                flush=True,
            )


if __name__ == "__main__":
    main()
