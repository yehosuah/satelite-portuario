#!/usr/bin/env python3
"""Reconcile a frozen historical campaign with its read-only catalog and files.

Exit zero means every planned acquisition is accounted for, published pairs and
jobs reconcile, and the requested evidence checks pass. It never means that
observations are cloud-free or that a model met a scientific accuracy target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_db(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def audit(data_dir, plan_dir, *, verify_hashes=False):
    data_dir, plan_dir = Path(data_dir), Path(plan_dir)
    plan_path = plan_dir / "plan.json"
    plan = json.loads(plan_path.read_text())
    with read_db(plan_dir / "progress.sqlite") as ledger:
        rows = [dict(row) for row in ledger.execute(
            "SELECT * FROM history_acquisitions ORDER BY acquisition_key")]
    with read_db(data_dir / "catalog.sqlite") as catalog:
        observations = [dict(row) for row in catalog.execute(
            "SELECT observation_id,acquisition_key,zone_id,recipe_version,port_id,sensor,"
            "status,review_status,model_id,detection_count FROM observations ORDER BY observation_id")]
        jobs = {row["observation_id"]: dict(row) for row in catalog.execute(
            "SELECT observation_id,status FROM jobs")}
        assets = [dict(row) for row in catalog.execute(
            "SELECT asset_id,observation_id,path,sha256,size_bytes,metadata_json FROM assets ORDER BY asset_id")]
        catalog_integrity = catalog.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in catalog.execute("PRAGMA foreign_key_check")]
    planned = {item["scene"]["acquisition_key"]: item for item in plan["acquisitions"]}
    ledger_by_key = {row["acquisition_key"]: row for row in rows}
    states = Counter(row["status"] for row in rows)
    version = plan["plan_id"] if "selected_acquisition_keys" in plan else plan["version"]
    published = [row for row in observations if (row["model_id"] or "").endswith("-" + version)]
    by_key = defaultdict(list)
    for row in published:
        by_key[row["acquisition_key"]].append(row)
    errors = []

    def check(condition, kind, **details):
        if not condition:
            errors.append({"kind": kind, **details})

    check(len(planned) == len(plan["acquisitions"]), "duplicate_plan_acquisitions")
    check(set(planned) == set(ledger_by_key), "ledger_plan_key_mismatch",
          missing=sorted(set(planned) - set(ledger_by_key)),
          unexpected=sorted(set(ledger_by_key) - set(planned)))
    check(catalog_integrity == "ok", "catalog_integrity", result=catalog_integrity)
    check(not foreign_keys, "catalog_foreign_keys", violations=foreign_keys)
    pair_counts = Counter()
    replay = defaultdict(list)
    for asset in assets:
        metadata = json.loads(asset["metadata_json"])
        if metadata.get("role") == "history_inference_manifest":
            replay[metadata["acquisition_key"]].append(asset)
    missing_replay = []
    exceptions = []
    last_attempt_seconds = 0
    for key, item in planned.items():
        row = ledger_by_key.get(key)
        if row is None:
            continue
        check(json.loads(row["plan_json"]) == item, "ledger_plan_content_mismatch", acquisition_key=key)
        zone_ids = [zone["properties"]["zone_id"] for zone in item["zones"]]
        check(len(zone_ids) == len(set(zone_ids)), "duplicate_plan_zones", acquisition_key=key)
        pair_counts[row["status"]] += len(zone_ids)
        actual = by_key[key]
        if row["status"] == "complete":
            check(Counter(zone_ids) == Counter(obs["zone_id"] for obs in actual),
                  "published_pairs_mismatch", acquisition_key=key,
                  expected=zone_ids, actual=[obs["zone_id"] for obs in actual])
            metrics = json.loads(row["metrics_json"])
            receipt_ids = {value["observation_id"] for value in metrics.get("observations", [])}
            check(receipt_ids == {obs["observation_id"] for obs in actual},
                  "ledger_receipt_mismatch", acquisition_key=key)
            last_attempt_seconds += metrics.get("total_seconds", 0)
            if len(replay[key]) != 1:
                missing_replay.append(key)
            else:
                record = replay[key][0]
                manifest_path = data_dir / record["path"]
                try:
                    manifest = json.loads(manifest_path.read_text())
                    check(digest(manifest_path) == record["sha256"], "replay_manifest_hash", acquisition_key=key)
                    check([entry["tile"] for entry in manifest["tiles"]] == item["tiles"],
                          "replay_plan_tiles", acquisition_key=key)
                except (OSError, ValueError, KeyError) as exc:
                    check(False, "replay_manifest_unreadable", acquisition_key=key, detail=str(exc))
            for obs in actual:
                check(jobs.get(obs["observation_id"], {}).get("status") == "succeeded",
                      "job_not_succeeded", observation_id=obs["observation_id"])
                check(obs["status"] in {"usable", "partial", "unusable"},
                      "observation_nonterminal", observation_id=obs["observation_id"])
                check(obs["sensor"] == item["scene"]["sensor"],
                      "observation_sensor_mismatch", observation_id=obs["observation_id"])
        elif row["status"] == "unavailable":
            check(not actual, "unavailable_has_published_observations", acquisition_key=key)
            check(bool(item.get("planning_error") or row["error_message"]),
                  "unexplained_unavailable", acquisition_key=key)
            exceptions.append({"acquisition_key": key, "zone_ids": zone_ids,
                               "reason": row["error_message"], "error_code": row["error_code"]})
        else:
            check(False, "acquisition_not_closed", acquisition_key=key, status=row["status"])
    check(not (set(by_key) - set(planned)), "unexpected_published_acquisition",
          acquisition_keys=sorted(set(by_key) - set(planned)))
    check(not missing_replay, "missing_complete_replay", acquisition_keys=missing_replay)
    protected = {row["observation_id"]: row for row in plan["protected_observations"]}
    observed_by_id = {row["observation_id"]: row for row in observations}
    for oid, reference in protected.items():
        actual = observed_by_id.get(oid, {})
        check(all(actual.get(field) == value for field, value in reference.items()),
              "protected_identity_mismatch", observation_id=oid)

    def check_asset(asset):
        path = data_dir / asset["path"]
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(data_dir.resolve()):
                return {"kind": "unsafe_asset", "path": asset["path"]}
            if path.stat().st_size != asset["size_bytes"]:
                return {"kind": "asset_size", "path": asset["path"]}
            if verify_hashes and digest(path) != asset["sha256"]:
                return {"kind": "asset_sha256", "path": asset["path"]}
        except OSError as exc:
            return {"kind": "asset_unreadable", "path": asset["path"], "detail": str(exc)}
        return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        file_errors = [problem for problem in executor.map(check_asset, assets) if problem]
    errors.extend(file_errors)
    baseline_path = plan_dir / "protected-baseline.json"
    protected_baseline_matches = None
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        current_assets = {asset["asset_id"]: asset for asset in assets}
        protected_baseline_matches = all(
            current_assets.get(asset["asset_id"], {}).get("sha256") == asset["sha256"]
            and current_assets.get(asset["asset_id"], {}).get("path") == asset["path"]
            for asset in baseline["assets"])
        check(protected_baseline_matches, "protected_asset_catalog_changed")
    unavailable_count = states.get("unavailable", 0)
    complete_count = states.get("complete", 0)
    return {
        "schema_version": "history-closure-audit-v1",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "plan_id": plan["plan_id"], "plan_sha256": digest(plan_path),
        "catalog_scope": "Only frozen-plan observations are reconciled; asset integrity covers the entire catalog snapshot.",
        "ledger_logical_sha256": canonical_digest(rows),
        "catalog_observations_logical_sha256": canonical_digest(observations),
        "all_registered_assets_logical_sha256": canonical_digest(assets),
        "acquisitions": {"planned": len(planned), "states": dict(states),
                         "completed_fraction": complete_count / len(planned) if planned else None,
                         "unavailable": unavailable_count,
                         "outstanding": len(rows) - complete_count - unavailable_count},
        "zone_observations": {"planned": sum(pair_counts.values()), "by_acquisition_state": dict(pair_counts),
                              "published": len(published), "states": dict(Counter(obs["status"] for obs in published)),
                              "protected_observations": len(protected), "catalog_total": len(observations)},
        "evidence": {"complete_acquisitions_with_replay": complete_count - len(missing_replay),
                     "missing_replay": missing_replay, "files_checked": len(assets),
                     "bytes_checked": sum(asset["size_bytes"] for asset in assets),
                     "sha256_verified": verify_hashes, "protected_asset_catalog_matches_baseline": protected_baseline_matches},
        "resources": {"download_bytes_reserved": sum(row["bytes_reserved"] for row in rows),
                      "last_successful_attempt_seconds_sum": last_attempt_seconds,
                      "attempts_total": sum(row["attempts"] for row in rows),
                      "last_completed_at": max((row["finished_at"] for row in rows if row["finished_at"]), default=None),
                      "timing_limit": "Sum excludes failed/interrupted attempts, earlier materialization and pauses; it is not total compute or wall time."},
        "exceptions": exceptions, "errors": errors, "passed": not errors,
        "scientific_validation": "Not evaluated; processed observations remain experimental proposals.",
        "pilot_completion": (
            {"separate_acquisitions": sorted({row["acquisition_key"] for row in protected.values()}),
             "status": "Excluded from this frozen campaign; original partial crops remain protected."}
            if "selected_acquisition_keys" not in plan else
            {"selected_protected_acquisitions": (plan.get("supplemental_authorization") or {}).get("acquisition_keys", []),
             "status": "Separate recipe; prior observations remain protected."}),
        "limits": ["Unavailable context is not a zero-vessel observation.",
                   "Complete processing does not imply full satellite footprint or cloud-free coverage.",
                   "Protected identity/asset checks do not assert that authorized later reviews never changed observation rows."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--plan-dir", type=Path, default=Path("data/history/full-v1"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()
    result = audit(args.data_dir, args.plan_dir, verify_hashes=args.verify_hashes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "acquisitions": result["acquisitions"],
                      "zone_observations": result["zone_observations"], "evidence": result["evidence"],
                      "errors": result["errors"], "output": str(args.output)}, indent=2))
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
