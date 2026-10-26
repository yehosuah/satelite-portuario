"""Small read-only snapshots for the local operator, without loading ML models."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from puertogt.refresh import freshness_report
from puertogt.store import Store, confined_path, utc_text

REQUIRED_TASKS = (
    ("detection", "sentinel-1"),
    ("detection", "sentinel-2"),
    ("attributes", "sentinel-1"),
    ("attributes", "sentinel-2"),
    ("change", "sentinel-2"),
)


def declares_local_validation(manifest: dict) -> bool:
    """Do not confuse an upstream/public benchmark with a local declaration."""
    policy = manifest.get("publication_policy", {})
    return bool(policy.get("status") == "validated"
                and policy.get("local_validation") == "passed"
                and manifest.get("weights_sha256") and manifest.get("dataset_version"))


def _campaigns(data_dir: Path) -> list[dict]:
    reports = []
    for path in sorted((data_dir / "history").glob("*/progress.sqlite")):
        report = {"campaign": path.parent.name, "readable": False}
        try:
            path = confined_path(data_dir, path.relative_to(data_dir))
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as tx:
                tx.execute("PRAGMA query_only=ON")
                tx.execute("BEGIN")
                counts = dict(tx.execute(
                    "SELECT status,count(*) FROM history_acquisitions GROUP BY status"
                ).fetchall())
                last_activity = tx.execute(
                    "SELECT max(stamp) FROM (SELECT started_at AS stamp FROM history_acquisitions "
                    "UNION ALL SELECT finished_at FROM history_acquisitions)"
                ).fetchone()[0]
            total = sum(counts.values())
            report.update(
                readable=True, counts=counts, total=total,
                complete=counts.get("complete", 0),
                outstanding=total - counts.get("complete", 0) - counts.get("unavailable", 0),
                unavailable=counts.get("unavailable", 0),
                pause_requested=(path.parent / "PAUSE").exists(),
                last_activity_at=last_activity,
            )
        except (OSError, ValueError, sqlite3.Error):
            # Do not expose source URLs, credentials, paths or raw exception text.
            report["error"] = "campaign_status_unavailable"
        reports.append(report)
    return reports


def system_status(store: Store) -> dict:
    with closing(store.connect()) as tx:
        tx.execute("BEGIN")
        acquisition_count = tx.execute("SELECT count(*) FROM acquisitions").fetchone()[0]
        observation_states = dict(tx.execute(
            "SELECT status,count(*) FROM observations GROUP BY status"
        ).fetchall())
        job_states = dict(tx.execute("SELECT status,count(*) FROM jobs GROUP BY status").fetchall())
        review_states = dict(tx.execute(
            "SELECT review_status,count(*) FROM observations GROUP BY review_status"
        ).fetchall())
        reviewed = tx.execute(
            "SELECT data_json FROM observations WHERE review_status='reviewed'"
        ).fetchall()
        models = [json.loads(row[0]) for row in tx.execute("SELECT data_json FROM models")]
    reviewed_scopes = sum(bool(json.loads(row[0]).get("scene_complete")) for row in reviewed)
    tasks = []
    for task, sensor in REQUIRED_TASKS:
        registered = [m for m in models if m.get("task") == task and m.get("sensor") == sensor]
        declared = [m for m in registered if declares_local_validation(m)]
        tasks.append({"task": task, "sensor": sensor, "registered_versions": len(registered),
                      "local_validation_declared": bool(declared)})
    return {
        "generated_at": utc_text(),
        "scope": "entire_local_volume",
        "catalog_acquisitions": acquisition_count,
        "observations": {"total": sum(observation_states.values()), "states": observation_states},
        "jobs": job_states,
        "reviews": {"states": review_states, "complete_scopes": reviewed_scopes},
        "history": _campaigns(store.data_dir),
        "freshness": freshness_report(store),
        "model_tasks": tasks,
        "limits": [
            "Processed acquisitions may contain unusable observations.",
            "Review counts include attributed AI reviews and abstentions, not only human labels.",
            "Local validation requires an explicit passed declaration in a registered manifest; "
            "this snapshot does not evaluate accuracy.",
            "Campaign counts are persisted state, not proof that a worker process is alive.",
        ],
    }
