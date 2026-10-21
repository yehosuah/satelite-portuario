"""Freeze the 22 visually inspected pilot observations with explicit AI provenance.

This script replays the two curated review reports; it does not infer labels from
model scores. Run ``prepare``, inspect the plan, then ``apply`` and ``freeze``.
Every revision preserves the original proposal. Existing versions are immutable.
"""

from __future__ import annotations

import argparse
import json
import shutil
from contextlib import closing
from pathlib import Path

from shapely.geometry import mapping, shape

from puertogt.review import export_dataset, record_review, validate_geometry
from puertogt.store import Store, confined_path, json_text, sha256_file, utc_text

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports/local-review"
PLAN = REPORTS / "consolidated-review.json"
AUTHOR = "codex-ai-root"
BATCH = "ai-visual-20261021-v1"


def read(path: Path):
    return json.loads(path.read_text())


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json_text(value) + "\n")


def prepare(store: Store) -> None:
    inspections = ROOT / "tmp/local-review/root-inspections.json"
    viewed = {(x["zone"], x["sensor"]) for x in read(inspections)}
    if len(viewed) != 22:
        raise ValueError("All 22 pilot zones must have an actual viewer inspection")
    zones = {x["properties"]["zone_id"]: shape(x["geometry"])
             for x in read(ROOT / "config/ports.geojson")["features"]}
    sources, actions, observations = [], [], []
    with closing(store.connect()) as tx:
        for sensor in ("s1", "s2"):
            path = REPORTS / f"{sensor}-independent-review.json"
            source = {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
            sources.append(source)
            for item in read(path)["observations"]:
                oid = item["observation_id"]
                row = tx.execute("SELECT * FROM observations WHERE observation_id=?", (oid,)).fetchone()
                if row is None or (item["zone_id"], sensor.upper()) not in viewed:
                    raise ValueError("Missing catalog observation or viewer inspection")
                if row["acquisition_key"] != item["acquisition_key"]:
                    raise ValueError("Acquisition changed after visual review")
                for asset in tx.execute("SELECT path,sha256 FROM assets WHERE observation_id=?", (oid,)):
                    if sha256_file(confined_path(store.data_dir, asset["path"])) != asset["sha256"]:
                        raise ValueError("Visual evidence changed")
                provenance = {
                    "kind": "ai_visual_review", "author": AUTHOR,
                    "second_reviewer": item["author"], "batch": BATCH,
                    "reference": source["path"], "reference_sha256": source["sha256"],
                    "viewer_inspections_sha256": sha256_file(inspections),
                    "human_review": False, "blind_to_model": False,
                    "scope": "explicit observable area only; excluded regions remain unknown",
                    "completeness_limit": "visually discernible vessels at the available resolution",
                }
                positives = item.get("existing_detection_decisions", item.get("existing_detections", []))
                for obj in positives:
                    decision = obj.get("decision", obj.get("proposed_decision"))
                    if decision not in {"accept", "aceptar"}:
                        raise ValueError("An unresolved decision requires manual consolidation")
                    geometry = obj.get("suggested_visual_geometry") or obj["suggested_visual_localization"]["geometry_wgs84"]
                    validate_geometry(geometry)
                    unique = obj.get("unique_physical_object_key", obj["detection_id"])
                    detection = tx.execute("SELECT observation_id FROM detections WHERE detection_id=?", (obj["detection_id"],)).fetchone()
                    if detection is None or detection[0] != oid:
                        raise ValueError("Detection does not belong to its reviewed observation")
                    actions.append({
                        "entity_type": "detection", "entity_id": obj["detection_id"],
                        "author": AUTHOR, "correction": {
                            "review_status": "reviewed", "geometry": geometry,
                            "family": None, "length_m": None, "width_m": None,
                            "note": "Revisión visual de IA: " + obj["reason"],
                            "provenance": {**provenance,
                                "unique_physical_object_key": item["acquisition_key"] + "|" + unique,
                                "geometry_role": "approximate_visual_box_not_physical_dimensions"},
                        },
                    })
                area = item.get("proposed_training_reviewed_area_wgs84")
                correction = {
                    "review_status": "reviewed", "scene_complete": bool(area),
                    "note": "Revisado por IA. " + item["summary"] + (
                        " Etiquetas completas solo dentro del área verde; el resto no se etiqueta como ausencia."
                        if area else " Sin ámbito suficientemente resoluble: excluido de entrenamiento y evaluación."
                    ),
                    "provenance": provenance,
                }
                split = None
                if area:
                    validate_geometry(area)
                    proposed = shape(area)
                    clipped = proposed.intersection(zones[item["zone_id"]])
                    removed = 1 - clipped.area / proposed.area
                    if removed > 0.001 or clipped.is_empty:
                        raise ValueError("Review scope extends materially beyond its configured zone")
                    # Straight edges in UTM and WGS84 differ by subpixel slivers.
                    # Clip those slivers instead of expanding the configured zone.
                    area = mapping(clipped)
                    provenance["scope_clipped_to_configured_zone_fraction"] = max(0, removed)
                    split = "test" if item["port_id"] == "prq" else "train"
                    correction.update(reviewed_area=area, dataset_split=split,
                                      split_groups=[item["acquisition_key"]])
                actions.append({"entity_type": "observation", "entity_id": oid,
                                "author": AUTHOR, "correction": correction})
                observations.append({
                    "observation_id": oid, "zone_id": item["zone_id"], "sensor": row["sensor"],
                    "acquisition_key": row["acquisition_key"], "dataset_split": split,
                    "eligible_reviewed_area": bool(area), "accepted_zone_labels": len(positives),
                })
    if len(observations) != 22 or len({o["observation_id"] for o in observations}) != 22:
        raise ValueError("Expected exactly the 22 original observations")
    write_new(PLAN, {
        "batch": BATCH, "author": AUTHOR, "created_at": utc_text(), "kind": "ai_visual_review",
        "sources": sources, "observations": observations, "actions": actions,
        "summary": {"observations_reviewed": 22, "eligible_areas": 19,
                    "excluded_radar_dock_observations": 3, "accepted_zone_labels": 5,
                    "unique_object_observations_across_dates": 4,
                    "acquisitions": 4, "new_omissions_confirmed": 0},
        "limitations": [
            "AI visual review, not human or independent reference validation",
            "Optical canal and anchorage include the same eastern vessel; not independent objects",
            "Ambiguous dock objects and truncated signatures remain unknown, not negatives",
            "All training areas in this first pilot contain zero confirmed positive labels",
            "No family, physical dimensions or change references are inferred",
        ],
    })
    print(json.dumps({"plan": str(PLAN), "sha256": sha256_file(PLAN), "actions": len(actions)}))


def apply(store: Store) -> None:
    plan = read(PLAN)
    for source in plan["sources"]:
        if sha256_file(ROOT / source["path"]) != source["sha256"]:
            raise ValueError("A curated review report changed after consolidation")
    revisions = []
    for action in plan["actions"]:
        with closing(store.connect()) as tx:
            previous = tx.execute(
                "SELECT revision_id,correction_json,author FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY rowid DESC LIMIT 1",
                (action["entity_type"], action["entity_id"]),
            ).fetchone()
        if previous:
            if read_json := json.loads(previous["correction_json"]):
                if read_json == action["correction"] and previous["author"] == AUTHOR:
                    revisions.append({"entity_id": action["entity_id"], "revision_id": previous["revision_id"]})
                    continue
            raise ValueError("A newer or different review exists; reconcile it before replaying")
        revision = record_review(store, action["entity_id"], action["correction"], AUTHOR,
                                 entity_type=action["entity_type"])
        revisions.append({"entity_id": action["entity_id"], "revision_id": revision})
    receipt = REPORTS / "applied-review.json"
    if not receipt.exists():
        write_new(receipt, {"batch": BATCH, "plan_sha256": sha256_file(PLAN),
                            "applied_at": utc_text(), "revisions": revisions})
    print(json.dumps({"applied_or_verified": len(revisions), "receipt": str(receipt)}))


def freeze(store: Store) -> None:
    root = store.data_dir / "datasets" / BATCH
    index = {"batch": BATCH, "consolidated_review_sha256": sha256_file(PLAN), "datasets": []}
    for split in ("train", "test"):
        combined = root / f"all-{split}"
        manifest = export_dataset(store, combined, split=split, version=f"{BATCH}-{split}")
        for short, sensor in (("s1", "sentinel-1"), ("s2", "sentinel-2")):
            selected = [sample for sample in manifest["samples"] if sample["sensor"] == sensor]
            if not selected:
                raise ValueError("Sensor split has no reviewed areas")
            target = root / f"{short}-{split}"
            target.mkdir(parents=True, exist_ok=False)
            for sample in selected:
                for relative in [sample["labels"], *(a["path"] for a in sample["evidence_assets"])]:
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(combined / relative, destination)
            child = {**manifest, "version": f"{BATCH}-{short}-{split}", "samples": selected,
                     "parent_manifest_sha256": sha256_file(combined / "manifest.json")}
            write_new(target / "manifest.json", child)
            index["datasets"].append({"path": str(target.relative_to(ROOT)), "sensor": sensor,
                                      "split": split, "samples": len(selected),
                                      "sha256": sha256_file(target / "manifest.json")})
    write_new(REPORTS / "frozen-datasets.json", index)
    print(json.dumps(index, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply", "freeze"))
    args = parser.parse_args()
    store = Store(ROOT / "data/catalog.sqlite", ROOT / "data")
    {"prepare": prepare, "apply": apply, "freeze": freeze}[args.action](store)


if __name__ == "__main__":
    main()
