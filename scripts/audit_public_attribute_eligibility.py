#!/usr/bin/env python3
"""Freeze numeric eligibility of every original public sample before fitting."""

import argparse
import json
from pathlib import Path

from puertogt.ml.public_attribute_eligibility import audit_numeric_datasets
from puertogt.ml.public_attributes import load_selection
from puertogt.store import sha256_file, utc_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, required=True)
    parser.add_argument("--amendment-sha256", required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if sha256_file(args.amendment) != args.amendment_sha256:
        raise ValueError("Numeric eligibility amendment checksum mismatch")
    amendment = json.loads(args.amendment.read_text())
    if (amendment.get("schema_version") != "public-attribute-numeric-eligibility-amendment-v1"
            or amendment.get("training_started") is not False
            or amendment.get("test_evaluation_started") is not False):
        raise ValueError("Numeric eligibility must be fixed before fitting and test evaluation")
    selection = load_selection(args.selection, amendment["selection_sha256"])
    report = audit_numeric_datasets(args.dataset_root, amendment["selection_sha256"])
    expected = {sample["window_id"]: sample["import_split"] for sample in selection["samples"]}
    if {row["source_window_id"]: row["split"] for row in report["rows"]} != expected:
        raise ValueError("Numeric audit must retain every originally selected ID and split")
    repository = Path(__file__).resolve().parents[1]
    source_names = ("src/puertogt/ml/public_attribute_eligibility.py", "scripts/train_public_attributes.py",
                    "scripts/audit_public_attribute_eligibility.py")
    report.update(schema_version="public-attribute-numeric-eligibility-report-v1", created_at=utc_text(),
                  amendment_sha256=args.amendment_sha256, experiment_plan_sha256=amendment["experiment_plan_sha256"],
                  source_sha256={name: sha256_file(repository / name) for name in source_names},
                  training_started=False, test_evaluation_started=False, targets_used_for_eligibility=False)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report_sha256": sha256_file(args.destination), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
