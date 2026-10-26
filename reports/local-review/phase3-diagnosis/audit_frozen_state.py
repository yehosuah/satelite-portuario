"""Audit prior manifests by bytes and external CAL/TEST state without reading labels."""

import json
from pathlib import Path

from puertogt.store import sha256_file, utc_text

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    baseline = ROOT / "data/history/pilot-completion-v1/frozen-manifests-before.json"
    expected = json.loads(baseline.read_text())
    if len(expected) != 26:
        raise ValueError("Unexpected protected baseline cohort")
    rows = []
    for relative, digest in sorted(expected.items()):
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or path.name != "manifest.json":
            raise ValueError("Unexpected baseline path")
        actual = sha256_file(path)
        rows.append({"path": relative, "baseline_sha256": digest, "current_sha256": actual,
                     "unchanged": digest == actual})
    calibration = ROOT / "data/experiments/phase2-s2-candidate-calibration-v1-20261023"
    old_completion = json.loads((calibration / "calibrate.completed.json").read_text())
    old_copy = ROOT / "reports/local-review/phase2/candidate-calibration-v1/calibrate.completed.json"
    cal_outputs = [{"path": str((calibration / relative).relative_to(ROOT)), "expected_sha256": digest,
                    "current_sha256": sha256_file(calibration / relative)}
                   for relative, digest in old_completion["outputs"].items()]
    runs = []
    for folder in ("train-anchor-comparison-v3-20261026", "train-anchor-refit-v1-20261026"):
        path = ROOT / "data/experiments" / folder
        completed = json.loads((path / "completed.json").read_text())
        markers = [name for name in ("calibrate.started.json", "evaluate.started.json", "test.started.json")
                   if (path / name).exists()]
        runs.append({"experiment": str(path.relative_to(ROOT)), "status": completed["status"],
                     "completed_sha256": sha256_file(path / "completed.json"),
                     "calibration_or_test_opened": completed["calibration_or_test_opened"],
                     "reserved_stage_markers": markers})
    registry = ROOT / "data/experiments/phase2-test-consumption"
    registry_files = sorted(str(path.relative_to(ROOT)) for path in registry.rglob("*") if path.is_file()) if registry.exists() else []
    external_state = {"previous_calibration_status": old_completion["status"],
        "previous_calibration_completion_matches_public_copy": sha256_file(calibration / "calibrate.completed.json") == sha256_file(old_copy),
        "previous_calibration_outputs": cal_outputs,
        "previous_calibration_test_start_present": (calibration / "evaluate.started.json").exists(),
        "test_consumption_registry_exists": registry.exists(), "test_consumption_registry_files": registry_files,
        "new_experiment_receipts": runs,
        "held_out_label_contents_read_by_this_audit": False,
        "method": "Manifest files are byte-hashed only. This audit reads completion receipts and file names, never TEST label geometries or TEST model outputs."}
    passed = (all(row["unchanged"] for row in rows)
              and old_completion["status"] == "failed"
              and external_state["previous_calibration_completion_matches_public_copy"]
              and all(item["expected_sha256"] == item["current_sha256"] for item in cal_outputs)
              and not external_state["previous_calibration_test_start_present"] and not registry_files
              and all(not item["calibration_or_test_opened"] and not item["reserved_stage_markers"] for item in runs))
    result = {"created_at": utc_text(), "script_sha256": sha256_file(Path(__file__)),
              "baseline": {"path": str(baseline.relative_to(ROOT)), "sha256": sha256_file(baseline)},
              "protected_manifest_count": len(rows), "protected_manifests": rows,
              "external_cal_test_state": external_state, "all_checks_passed": passed,
              "supplemental_train_manifest": {"path": "data/datasets/phase3-reviews/s2-caribe-march-april-v1/source/manifest.json",
                  "sha256": sha256_file(ROOT / "data/datasets/phase3-reviews/s2-caribe-march-april-v1/source/manifest.json")}}
    output = HERE / "frozen-state-audit-v1.json"
    with output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"all_checks_passed": passed, "protected_manifest_count": len(rows),
                      "sha256": sha256_file(output)}))
    if not passed:
        raise RuntimeError("Frozen state audit failed; preserve report and investigate")


if __name__ == "__main__":
    main()
