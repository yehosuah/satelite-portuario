"""Frozen two-recipe TRAIN-only comparison of anchor geometry, never validation."""

from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import shutil
import signal
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from train_phase2 import inspect_split, runtime_identity, source_hashes, write_json
from train_phase2_amendment import (
    _measure,
    _seed,
    _train_manifest,
    assert_disjoint,
    select_recipe,
)

from puertogt.cli import read_model
from puertogt.history import file_lock
from puertogt.ml.detection import detector_anchor_sizes
from puertogt.ml.workflows import train_detector
from puertogt.store import sha256_file, utc_text

ROOT = Path(os.environ.get("PUERTOGT_WORKSPACE", Path(__file__).resolve().parents[1])).resolve()


@contextmanager
def _heavy_budget(seconds):
    if seconds <= 0:
        raise TimeoutError("Predeclared heavy budget exhausted")

    def exceeded(signum, frame):
        raise TimeoutError("Predeclared heavy budget exhausted")

    previous = signal.signal(signal.SIGALRM, exceeded)
    try:
        with file_lock(ROOT / "data/.heavy-worker.lock"):
            signal.setitimer(signal.ITIMER_REAL, seconds)
            yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def validate_plan(plan):
    if plan.get("schema_version") != "train-anchor-comparison-v1":
        raise ValueError("A preregistered TRAIN anchor comparison is required")
    expected = [{"name": "baseline_anchors", "anchor_sizes": [[32], [64], [128], [256]],
                 "epochs": 12, "learning_rate": .0001, "sample_order": "shuffle"},
                {"name": "small_anchors", "anchor_sizes": [[8], [16], [32], [64]],
                 "epochs": 12, "learning_rate": .0001, "sample_order": "shuffle"}]
    if plan.get("candidates") != expected:
        raise ValueError("Exactly the two predefined anchor recipes are permitted")
    if (plan.get("fixed", {}).get("seed") != 20261021
            or plan["fixed"].get("score_threshold") != .5
            or plan["fixed"].get("iou_threshold") != .5
            or plan["fixed"].get("cpu_threads") != 4
            or plan["fixed"].get("encoder_frozen") is not True):
        raise ValueError("Fixed TRAIN comparison settings changed")
    for name in ("training", "development", "refit"):
        if plan.get(name, {}).get("split") != "train":
            raise ValueError("Every comparison input must explicitly be TRAIN")
    if plan.get("calibration_or_test_permitted") is not False:
        raise ValueError("CAL and TEST must remain forbidden")
    if plan.get("refit_permitted") is not False:
        raise ValueError("This comparison stops after internal development; refit needs a separate decision")


def validate_inputs(plan, base):
    checked = {}
    for name in ("training", "development", "refit"):
        descriptor = plan[name]
        root = ROOT / descriptor["path"]
        _train_manifest(root)
        source = ROOT / descriptor["source_path"] if descriptor.get("source_path") else None
        if source:
            _train_manifest(source)
            if sha256_file(source / "manifest.json") != descriptor["source_sha256"]:
                raise ValueError("TRAIN source manifest changed")
        _, checked[name] = inspect_split(root, descriptor["sha256"], "train", base,
                                         train_source=source)
    assert_disjoint(checked["training"], checked["development"])
    return checked


def archive_sources(output):
    paths = list((ROOT / "src/puertogt").rglob("*.py")) + [Path(__file__),
        ROOT / "scripts/train_phase2.py", ROOT / "scripts/train_phase2_amendment.py",
        ROOT / "uv.lock", ROOT / "pyproject.toml", ROOT / "config/models/s2.yaml"]
    pins = {}
    for path in paths:
        relative = str(path.relative_to(ROOT))
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        pins[relative] = sha256_file(target)
        if sha256_file(path) != pins[relative]:
            raise ValueError("Source changed while freezing")
    return pins


def freeze(plan_path, expected_sha, output):
    if sha256_file(plan_path) != expected_sha:
        raise ValueError("Explicit preregistration checksum mismatch")
    plan = json.loads(plan_path.read_text())
    validate_plan(plan)
    base = read_model(ROOT / "config/models/s2.yaml", ROOT / "data")
    if base.weights_sha256 != plan["fixed"]["public_initialization_sha256"]:
        raise ValueError("Pinned public initialization changed")
    validate_inputs(plan, base)
    output.mkdir(parents=True, exist_ok=False)
    # Preserve the reviewed bytes: reserializing changes a valid plan's checksum.
    shutil.copyfile(plan_path, output / "plan.json")
    write_json(output / "base-manifest.json", base.model_dump(mode="json"))
    receipt = {"created_at": utc_text(), "plan_sha256": expected_sha,
               "base_manifest_sha256": sha256_file(output / "base-manifest.json"),
               "source_sha256": source_hashes(), "runtime": runtime_identity(),
               "archived_sources": archive_sources(output),
               "train_development_disjoint_by_ids_groups_numeric_hashes": True,
               "calibration_or_test_opened": False, "automatic_publication": False}
    write_json(output / "freeze.json", receipt)
    return {"frozen": True, "freeze_sha256": sha256_file(output / "freeze.json")}


def verify_frozen(output):
    receipt = json.loads((output / "freeze.json").read_text())
    if (sha256_file(output / "plan.json") != receipt["plan_sha256"]
            or sha256_file(output / "base-manifest.json") != receipt["base_manifest_sha256"]
            or runtime_identity() != receipt["runtime"] or source_hashes() != receipt["source_sha256"]):
        raise ValueError("Frozen plan, model, runtime or inference sources changed")
    for relative, digest in receipt["archived_sources"].items():
        if sha256_file(output / "source" / relative) != digest:
            raise ValueError("Archived source changed")
    if (sha256_file(Path(__file__)) != receipt["archived_sources"]["scripts/train_anchor_comparison.py"]
            or sha256_file(Path(sys.modules["train_phase2_amendment"].__file__))
            != receipt["archived_sources"]["scripts/train_phase2_amendment.py"]):
        raise ValueError("Execution runner changed; use the archived source")
    plan = json.loads((output / "plan.json").read_text())
    validate_plan(plan)
    base = read_model(output / "base-manifest.json", ROOT / "data")
    validate_inputs(plan, base)
    return plan, base, receipt


def configured_base(base, candidate, plan_sha):
    model = base.model_copy(update={"metadata": {**base.metadata,
        "anchor_sizes": candidate["anchor_sizes"], "anchor_experiment_plan_sha256": plan_sha}})
    detector_anchor_sizes(model)
    return model


def run(output):
    if any((output / name).exists() for name in ("started.json", "completed.json", "failed.json")):
        raise FileExistsError("A frozen comparison attempt cannot be repeated")
    plan, base, receipt = verify_frozen(output)
    # Exclusive creation also protects concurrent starts after the early check.
    write_json(output / "started.json", {"started_at": utc_text(),
                                         "freeze_sha256": sha256_file(output / "freeze.json")})
    elapsed, rows = 0., []
    budget = plan["budget"]
    try:
        for candidate in plan["candidates"]:
            verify_frozen(output)
            if shutil.disk_usage(ROOT).free < budget["minimum_free_disk_bytes"]:
                raise OSError("Insufficient disk reserve")
            destination = output / candidate["name"]
            planned = len(_train_manifest(ROOT / plan["training"]["path"])["samples"]) * candidate["epochs"]
            if planned > budget["maximum_steps_per_inner_recipe"]:
                raise ValueError("Training step budget exceeded")
            model = configured_base(base, candidate, receipt["plan_sha256"])
            start = time.monotonic()
            with _heavy_budget(budget["maximum_heavy_seconds"] - elapsed):
                _seed()
                write_json(destination / "started.json", {"candidate": candidate, "planned_steps": planned})
                train_detector(ROOT / plan["training"]["path"], model, destination / "trained",
                               epochs=candidate["epochs"], learning_rate=candidate["learning_rate"],
                               sample_order="shuffle", seed=plan["fixed"]["seed"])
                trained = read_model(destination / "trained/manifest.json", ROOT / "data")
                if detector_anchor_sizes(trained) != candidate["anchor_sizes"]:
                    raise ValueError("Exported anchor geometry changed before inference")
                report = _measure(ROOT / plan["development"]["path"], trained, destination / "development")
                rows.append({**candidate, "weights_sha256": trained.weights_sha256,
                             "overall": report["overall"], "ports": report["ports"]})
                write_json(destination / "completed.json", {"seconds": time.monotonic() - start,
                    "weights_sha256": trained.weights_sha256, "anchor_sizes": detector_anchor_sizes(trained),
                    "validation_eligible": False})
                del trained
                gc.collect()
            elapsed += time.monotonic() - start
            print(json.dumps({"candidate_completed": candidate["name"], "heavy_seconds": elapsed}), flush=True)
        decision = select_recipe(rows)
        write_json(output / "selection.json", decision)
        result = {"status": "completed_internal_train_development_only", "plan_sha256": receipt["plan_sha256"],
                  "freeze_sha256": sha256_file(output / "freeze.json"), "heavy_seconds": elapsed,
                  "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                  * (1 if sys.platform == "darwin" else 1024), "decision": decision, "refit": None,
                  "calibration_or_test_opened": False, "local_validation_demonstrated": False,
                  "automatic_publication": False}
        write_json(output / "completed.json", result)
        return result
    except BaseException as exc:
        write_json(output / "failed.json", {"error_type": type(exc).__name__, "message": str(exc),
                                             "immutable_attempt": True, "completed_candidates": rows})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("freeze")
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--plan-sha256", required=True)
    command.add_argument("--output", type=Path, required=True)
    commands.add_parser("run").add_argument("experiment", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.plan.resolve(), args.plan_sha256, args.output.resolve())
    else:
        result = run(args.experiment.resolve())
    print(json.dumps(result, indent=2))
