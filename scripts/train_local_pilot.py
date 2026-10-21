"""One preregistered detector adjustment per sensor against frozen AI labels.

No database writes, downloads, threshold search, attribute training, or deployment.
Run with ``uv run --no-sync python scripts/train_local_pilot.py --help``.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import json
import platform
import random
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "reports/local-review/training-plan.md"
PROTOCOL_SHA256 = "003623ec7d2b352a21a418c1c1338183985895ddffb63b6652e242cafb755d8d"
SEED, EPOCHS, LEARNING_RATE, THREADS = 20261021, 3, 2e-5, 4
IOU, MIN_SUPPORT = 0.5, 30
RECIPES = {
    "s1": {
        "sensor": "sentinel-1",
        "threshold": 0.7,
        "weights_sha256": "35645431fb58c410603ea3e4af4c7654218389fc8bf953fe3d7f83e5629b5d2a",
        "train": "S1D:IW:GRDH:1SDV:20261018T114526:20261018T114551:005069:009972",
        "test": "S1D:IW:GRDH:1SDV:20261019T001342:20261019T001407:005077:0099B2",
    },
    "s2": {
        "sensor": "sentinel-2",
        "threshold": 0.8,
        "weights_sha256": "99f9006a8f0d3ca1e5426567713ef03533d9378d1f70ecdb4990b8a98a69e9fe",
        "train": "S2B:MSIL1C:20260930T162059:R040:T16PCC",
        "test": "S2B:MSIL1C:20260910T161859:R040:T15PYR",
    },
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write_json(path: Path, value: dict | list) -> None:
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def inspect_split(root: Path, expected_hash: str, split: str, recipe: dict) -> tuple[dict, dict]:
    """Check frozen integrity and metadata; do not inspect test reference contents."""
    from puertogt.ml.workflows import reviewed_area
    from puertogt.store import confined_path

    path = root / "manifest.json"
    if digest(path) != expected_hash:
        raise ValueError(f"Frozen {split} manifest hash does not match")
    dataset = json.loads(path.read_text())
    if dataset.get("split") != split or not dataset.get("version") or not dataset.get("samples"):
        raise ValueError(f"A versioned, nonempty {split} dataset is required")
    ids, groups, numeric_hashes, ports = set(), set(), set(), set()
    for sample in dataset["samples"]:
        if (
            sample.get("sensor") != recipe["sensor"]
            or sample.get("acquisition_key") != recipe[split]
        ):
            raise ValueError(f"Sample violates preregistered sensor/acquisition for {split}")
        if sample.get("split", split) != split:
            raise ValueError("Conflicting sample split")
        port = sample.get("port_id")
        if port not in ({"stc", "pbr"} if split == "train" else {"prq"}):
            raise ValueError("Sample violates preregistered port holdout")
        ports.add(port)
        if sample["observation_id"] in ids:
            raise ValueError("Duplicate observation in frozen split")
        ids.add(sample["observation_id"])
        groups.update([sample["acquisition_key"], *sample.get("split_groups", [])])
        provenance = sample.get("reference_provenance", {})
        if provenance.get("kind") != "ai_visual_review" or not provenance.get("author"):
            raise ValueError("Explicit ai_visual_review provenance and author are required")
        if reviewed_area(sample) is None:
            raise ValueError("Every sample requires an explicit frozen reviewed_area")
        if digest(confined_path(root, sample["labels"])) != sample["sha256"]:
            raise ValueError("Frozen reference checksum mismatch")
        sample_numeric = set()
        for asset in sample.get("evidence_assets", []):
            if digest(confined_path(root, asset["path"])) != asset["sha256"]:
                raise ValueError("Frozen evidence checksum mismatch")
            bundle = asset.get("metadata", {}).get("bundle", {})
            if bundle.get("bands"):
                sample_numeric.add(asset["sha256"])
                historical = bundle.get("metadata", {}).get("historical_scene_id")
                if historical:
                    groups.add(historical)
        if not sample_numeric:
            raise ValueError("Sample has no hash-pinned numeric bands")
        numeric_hashes.update(sample_numeric)
    return dataset, {
        "ids": ids,
        "groups": groups,
        "numeric_hashes": numeric_hashes,
        "ports": ports,
    }


def inspect_pair(args, recipe: dict) -> tuple[dict, dict]:
    if args.protocol_sha256 != PROTOCOL_SHA256 or digest(PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError("The preregistered protocol has changed")
    train, left = inspect_split(args.train, args.train_sha256, "train", recipe)
    test, right = inspect_split(args.test, args.test_sha256, "test", recipe)
    for field in ("ids", "groups", "numeric_hashes"):
        if left[field].intersection(right[field]):
            raise ValueError(f"Train/test leakage through {field}")
    return train, test


def score_once(dataset_dir: Path, model, destination: Path, recipe: dict) -> dict:
    from puertogt.ml.evaluation import detection_report, match_detections, publication_decision
    from puertogt.ml.workflows import evaluation_inputs

    started = time.monotonic()
    dataset, inputs = evaluation_inputs(dataset_dir, model, required_split="test", device="cpu")
    counts, per_observation = [], []
    for item in inputs:
        sample = item["sample"]
        count = {
            **{key: sample[key] for key in ("port_id", "zone_id", "sensor")},
            **match_detections(item["predictions"], item["references"], iou_threshold=IOU),
        }
        counts.append(count)
        per_observation.append(
            {
                "observation_id": sample["observation_id"],
                "acquisition_key": sample["acquisition_key"],
                "reviewed_area": sample["reviewed_area"],
                "reference_provenance": sample["reference_provenance"],
                "counts": count,
                "predictions": item["predictions"],
                "reference_count": len(item["references"]),
            }
        )
    report = detection_report(
        counts,
        split="test",
        dataset_hash=digest(dataset_dir / "manifest.json"),
        independent_reference=False,
        test_frozen=True,
    )
    required = [
        f"prq|{recipe['sensor']}|prq_{zone}" for zone in ("canal", "fondeo", "muelles", "patios")
    ]
    report.update(
        {
            "model_id": model.model_id,
            "model_version": model.version,
            "weights_sha256": model.weights_sha256,
            "score_threshold": recipe["threshold"],
            "iou_threshold": IOU,
            "seconds": time.monotonic() - started,
            "reference_kind": "ai_visual_review",
            "label_author": "AI; see per-observation provenance",
            "independent_acquisition_count": len(
                {s["acquisition_key"] for s in dataset["samples"]}
            ),
            "reviewed_area_scope": "only frozen observable reviewed polygons; exclusions are not negatives",
            "publication_decision": publication_decision(
                report, required_strata=required, min_support=MIN_SUPPORT
            ),
            "scientific_status": "experimental; AI reference, one test acquisition; no human validation",
            "ai_reference_precision_assessment": {
                "target": 0.9,
                "minimum_support_per_stratum": MIN_SUPPORT,
                "eligible_strata": [
                    key
                    for key, metric in report["strata"].items()
                    if min(metric["prediction_count"], metric["reference_count"]) >= MIN_SUPPORT
                    and metric["precision"] is not None
                    and metric["precision"] >= 0.9
                ],
                "missing_strata": sorted(set(required).difference(report["strata"])),
                "scope": "Precision against the frozen AI labels only; not independent accuracy",
            },
        }
    )
    write_json(destination / "predictions.json", per_observation)
    write_json(destination / "metrics.json", report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--sensor", choices=sorted(RECIPES), required=True)
    result.add_argument("--train", type=Path, required=True)
    result.add_argument("--test", type=Path, required=True)
    result.add_argument("--train-sha256", required=True)
    result.add_argument("--test-sha256", required=True)
    result.add_argument("--protocol-sha256", required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument(
        "--check-only",
        action="store_true",
        help="Verify frozen metadata/hashes without training or viewing test labels",
    )
    return result


def main() -> None:
    args = parser().parse_args()
    args.train, args.test, args.output = (
        args.train.resolve(),
        args.test.resolve(),
        args.output.resolve(),
    )
    recipe = RECIPES[args.sensor]
    train, test = inspect_pair(args, recipe)
    preflight = {
        "sensor": recipe["sensor"],
        "train_observations": len(train["samples"]),
        "test_observations": len(test["samples"]),
        "target_acquisitions_per_split": 1,
        "train_manifest_sha256": args.train_sha256,
        "test_manifest_sha256": args.test_sha256,
        "protocol_sha256": PROTOCOL_SHA256,
        "author": "Codex (AI)",
    }
    if args.check_only:
        print(json.dumps({"preflight": preflight, "trained": False}, indent=2))
        return
    if args.output.exists():
        raise FileExistsError(
            "Use a new output directory; this script never reruns or overwrites a test"
        )

    import numpy as np
    import torch

    from puertogt.cli import read_model
    from puertogt.ml.workflows import labels_for_sample, train_detector

    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(THREADS)
    base = read_model(ROOT / f"config/models/{args.sensor}.yaml", ROOT / "data")
    if base.weights_sha256 != recipe["weights_sha256"] or base.sensor != recipe["sensor"]:
        raise ValueError("The public base checkpoint differs from the preregistered model")
    base = base.model_copy(
        update={
            "publication_policy": {
                **base.publication_policy,
                "score_threshold": recipe["threshold"],
            }
        }
    )
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "preflight.json", preflight)
    started = time.monotonic()
    support = [len(labels_for_sample(args.train, sample)) for sample in train["samples"]]
    runtime = {
        "python": sys.version,
        "platform": platform.platform(),
        "torch": str(torch.__version__),
        "numpy": str(np.__version__),
        "device": "cpu",
        "threads": THREADS,
        "seed": SEED,
        "source_sha256": {},
    }
    for name in ("detection", "training", "workflows", "evaluation"):
        module = importlib.import_module(f"puertogt.ml.{name}")
        runtime["source_sha256"][name] = {
            "path": module.__file__,
            "sha256": digest(Path(module.__file__)),
        }
    write_json(
        args.output / "run-started.json",
        {
            "runtime": runtime,
            "epochs": EPOCHS,
            "learning_rate": LEARNING_RATE,
            "training_reference_counts": support,
            "training_without_positives": sum(support) == 0,
            "script_sha256": digest(Path(__file__)),
            "protocol_sha256": PROTOCOL_SHA256,
        },
    )
    trained = train_detector(
        args.train,
        base,
        args.output / "trained",
        epochs=EPOCHS,
        learning_rate=LEARNING_RATE,
        device="cpu",
        reserved_manifests=[args.test / "manifest.json"],
    )
    trained = trained.model_copy(
        update={
            "model_id": f"local-ai-review-{args.sensor}",
            "version": "20261021-v1",
            "publication_policy": {
                "status": "experimental",
                "automatic_publication": False,
                "local_validation": "pending_ai_reference_evaluation",
                "score_threshold": recipe["threshold"],
            },
            "metadata": {
                **trained.metadata,
                "author": "Codex (AI)",
                "label_kind": "ai_visual_review",
                "protocol_sha256": PROTOCOL_SHA256,
                "seed": SEED,
            },
        }
    )
    (args.output / "trained/manifest.json").write_text(trained.model_dump_json(indent=2) + "\n")
    trained = read_model(args.output / "trained/manifest.json", ROOT / "data")
    # Recheck frozen hashes before the single reserved evaluation, without changing parameters.
    inspect_pair(args, recipe)
    write_json(
        args.output / "test-evaluation.started.json",
        {"test_manifest_sha256": args.test_sha256, "protocol_sha256": PROTOCOL_SHA256},
    )
    reports = {}
    for name, model in (("base", base), ("adjusted", trained)):
        (args.output / name).mkdir()
        reports[name] = score_once(args.test, model, args.output / name, recipe)
        gc.collect()
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    comparison = {
        "preflight": preflight,
        "runtime": runtime,
        "base": reports["base"],
        "adjusted": reports["adjusted"],
        "seconds": time.monotonic() - started,
        "peak_rss_bytes": max_rss if sys.platform == "darwin" else max_rss * 1024,
        "training_reference_counts": support,
        "training_without_positives": sum(support) == 0,
        "model_selection_performed": False,
        "threshold_calibration_performed": False,
        "automatic_publication": False,
        "attributes_trained": False,
        "changes_trained": False,
        "limitations": [
            "AI visual references, not human or independent ground truth",
            "Only one target acquisition in each split per sensor",
            "Multiple zones/crops do not increase independent acquisition support",
            "Only frozen reviewed areas are evaluated; excluded regions remain unknown",
            "No model/epoch/threshold selection from held-out outcomes",
        ],
    }
    write_json(args.output / "comparison.json", comparison)
    manifest_path = args.output / "trained/manifest.json"
    exported = json.loads(manifest_path.read_text())
    exported["publication_policy"]["local_validation"] = "evaluated_against_ai_labels_experimental"
    exported["metrics"] = reports["adjusted"]
    manifest_path.write_text(
        json.dumps(exported, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "base": reports["base"]["overall"],
                "adjusted": reports["adjusted"]["overall"],
                "automatic_publication": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
