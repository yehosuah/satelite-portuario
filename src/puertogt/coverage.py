"""Reconcile every catalogued acquisition/zone against each recipe independently."""

import json
from contextlib import closing
from pathlib import Path

from shapely.geometry import shape

from puertogt.store import Store


def coverage_report(
    store: Store, ports_path: Path, *, port_id: str | None = None, recipe_version: str | None = None
) -> dict:
    zones = json.loads(ports_path.read_text()).get("features", []) if ports_path.is_file() else []
    with closing(store.connect()) as tx:
        acquisitions = tx.execute(
            "SELECT * FROM acquisitions WHERE captured_at>='2025-01-01' ORDER BY captured_at"
        ).fetchall()
        observations = tx.execute("SELECT * FROM observations").fetchall()
    indexed = {}
    versions_by_sensor = {}
    for row in observations:
        indexed[(row["acquisition_key"], row["zone_id"], row["recipe_version"])] = row
        versions_by_sensor.setdefault(row["sensor"], set()).add(row["recipe_version"])
    counts = {}
    exceptions = []
    for acquisition in acquisitions:
        if json.loads(acquisition["metadata_json"]).get("context_only"):
            continue
        footprint = shape(json.loads(acquisition["footprint_json"]))
        for feature in zones:
            props = feature["properties"]
            if (port_id and props["port_id"] != port_id) or not footprint.intersects(
                shape(feature["geometry"])
            ):
                continue
            versions = (
                {recipe_version}
                if recipe_version
                else versions_by_sensor.get(acquisition["sensor"], {None})
            )
            for version in sorted(versions, key=lambda value: value or ""):
                key = (
                    acquisition["captured_at"][:7],
                    props["port_id"],
                    props["zone_id"],
                    acquisition["sensor"],
                    version,
                )
                item = counts.setdefault(
                    key,
                    dict(zip(("month", "port_id", "zone_id", "sensor", "recipe_version"), key))
                    | {
                        "catalogued": 0,
                        "processed": 0,
                        "usable": 0,
                        "partial": 0,
                        "unusable": 0,
                        "pending": 0,
                        "failed": 0,
                    },
                )
                item["catalogued"] += 1
                observation = indexed.get(
                    (acquisition["acquisition_key"], props["zone_id"], version)
                )
                status = observation["status"] if observation else "pending"
                if status in {"usable", "partial", "unusable"}:
                    item["processed"] += 1
                    item[status] += 1
                elif status == "failed":
                    item["failed"] += 1
                else:
                    item["pending"] += 1
                if status not in {"usable", "partial"}:
                    exceptions.append(
                        {
                            "acquisition_key": acquisition["acquisition_key"],
                            "port_id": props["port_id"],
                            "zone_id": props["zone_id"],
                            "sensor": acquisition["sensor"],
                            "recipe_version": version,
                            "captured_at": acquisition["captured_at"],
                            "status": status,
                            "reason": "not_scheduled" if observation is None else status,
                            "quality_flags": json.loads(observation["quality_flags_json"])
                            if observation
                            else [],
                        }
                    )
    return {
        "items": list(counts.values()),
        "exceptions": exceptions,
        "note": "Catalog availability is not usable coverage; recipe versions are separate.",
    }
