"""Consolidate explicitly adjudicated AI reviews, preserving evidence and audit history.

The adjudication is a separate authored JSON listing reviewed observation IDs and
hash-pinned reports. This importer never derives references from model scores.
"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from puertogt.review import add_detection, record_review, validate_geometry
from puertogt.store import Store, confined_path, json_text, sha256_file, utc_text

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json_text(value) + "\n")


def pinned(path):
    return {"path": str(path.resolve().relative_to(ROOT)), "sha256": sha256_file(path)}


def verify_sources(plan):
    for source in plan["sources"]:
        if sha256_file(confined_path(ROOT, source["path"])) != source["sha256"]:
            raise ValueError("A review or evidence source changed after adjudication")


def phase2_selection(selection_path, order_path):
    selection, order = read(selection_path), read(order_path)
    active = {(item["split"], port, item["sensor"], item["reserve_acquisition_key"])
              for item in order["activated_reserves"] for port in item["port_ids"]}
    result = {}
    for slot in selection["slots"]:
        candidates = ([slot["selected"]] if slot["selected"] else []) + [
            item for item in slot["reserves"]
            if (slot["split"], slot["port_id"], slot["sensor"], item["acquisition_key"]) in active]
        for item in candidates:
            key = (item["acquisition_key"], slot["port_id"], slot["sensor"])
            value = {"split": slot["split"], "context": (item.get("context") or {}).get("acquisition_key")}
            if key in result and result[key] != value:
                raise ValueError("Contradictory temporal selection")
            result[key] = value
    return result


def pass_id(key):
    parts = key.split(":")
    if parts[0].startswith("S1") and len(parts) == 8:
        return ":".join((parts[0], parts[6], parts[7]))
    if parts[0].startswith("S2") and len(parts) == 5:
        return ":".join((parts[0], parts[2], parts[3]))
    raise ValueError("Unsupported acquisition identity")


def polygonal_parts(geometry):
    """Keep surface components without turning tangent lines or points into area."""
    if geometry.geom_type == "Polygon":
        if geometry.area > 0:
            yield geometry
    elif geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from polygonal_parts(part)


def prepare(store, adjudication_path, destination, *, order_path=None):
    adjudication = read(adjudication_path)
    author = adjudication["author"]
    if not author or adjudication.get("kind") != "ai_visual_review" or adjudication.get("is_human_review") is not False:
        raise ValueError("Explicit AI adjudication and author are required")
    batch = adjudication["batch"]
    reports, sources = {}, [pinned(adjudication_path)]
    for source in adjudication["sources"]:
        path = confined_path(ROOT, source["path"])
        if sha256_file(path) != source["sha256"]:
            raise ValueError("Adjudication source checksum mismatch")
        sources.append(source)
        # Later addenda explicitly replace complete observation proposals.
        for observation in read(path)["observations"]:
            reports[observation["observation_id"]] = observation
    ids = adjudication["accepted_observation_ids"]
    if not ids or len(set(ids)) != len(ids) or set(ids) - reports.keys():
        raise ValueError("Adjudication must identify a unique reviewed cohort")
    selection_path = ROOT / "reports/local-review/phase2/sampling-plan.json"
    order_path = Path(order_path) if order_path is not None else ROOT / "reports/local-review/phase2/execution-order.json"
    sources.extend([pinned(selection_path), pinned(order_path)])
    selection = phase2_selection(selection_path, order_path)
    config = ROOT / "config/ports.geojson"
    sources.append(pinned(config))
    zones = {item["properties"]["zone_id"]: shape(item["geometry"])
             for item in read(config)["features"]}
    actions, observations, evidence = [], [], {}
    with closing(store.connect()) as tx:
        for oid in ids:
            item = reports[oid]
            row = tx.execute("SELECT * FROM observations WHERE observation_id=?", (oid,)).fetchone()
            if row is None or any(row[field] != item[field] for field in
                                  ("acquisition_key", "port_id", "zone_id", "sensor")):
                raise ValueError("Review no longer matches its catalog observation")
            selected = selection[(row["acquisition_key"], row["port_id"], row["sensor"])]
            for asset in item["evidence_assets"]:
                path = confined_path(ROOT, asset["path"])
                if sha256_file(path) != asset["sha256"]:
                    raise ValueError("Reviewed numeric evidence changed")
                evidence[asset["path"]] = asset["sha256"]
            provenance = {
                "kind": "ai_visual_review", "author": author, "second_reviewer": item["author"],
                "human_review": False, "batch": batch,
                "adjudication": str(adjudication_path.relative_to(ROOT)),
                "adjudication_sha256": sha256_file(adjudication_path),
                "blind_to_model": adjudication.get("blind_to_model", False),
                "exposure": adjudication["exposure"],
                "scope": "Only explicit reviewed_area; excluded areas remain unknown",
                "completeness_limit": "Visually discernible vessels at available resolution",
            }
            area = item.get("proposed_training_reviewed_area_wgs84")
            if area:
                validate_geometry(area)
                proposed = shape(area)
                clipped = unary_union(list(polygonal_parts(
                    proposed.intersection(zones[row["zone_id"]]))))
                if clipped.is_empty or clipped.area <= 0:
                    raise ValueError("Review area has no polygonal surface inside configured zone")
                removed = 1 - clipped.area / proposed.area
                if removed > 0.001:
                    raise ValueError("Review area extends materially beyond configured zone")
                area = mapping(clipped)
                provenance["scope_clipped_to_configured_zone_fraction"] = max(0, removed)
            accepted = 0
            for obj in item.get("existing_detections", item.get("existing_detection_decisions", [])):
                decision = obj.get("proposed_decision", obj.get("decision"))
                status = {"aceptar": "reviewed", "accept": "reviewed", "reject": "rejected", "rechazar": "rejected"}.get(decision)
                if status is None:
                    continue  # Export will reject unresolved proposals overlapping scope.
                detection = tx.execute("SELECT observation_id FROM detections WHERE detection_id=?", (obj["detection_id"],)).fetchone()
                if detection is None or detection[0] != oid:
                    raise ValueError("Detection belongs to a different observation")
                geometry = obj.get("suggested_visual_geometry") or obj.get("suggested_visual_localization", {}).get("geometry_wgs84")
                correction = {"review_status": status, "family": None, "length_m": None, "width_m": None,
                              "note": "Revisión visual de IA: " + obj["reason"], "provenance": provenance.copy()}
                if status == "reviewed":
                    validate_geometry(geometry)
                    if not area or not shape(area).covers(shape(geometry).centroid):
                        raise ValueError("Accepted positive center is outside reviewed scope")
                    correction["geometry"] = geometry
                    correction["provenance"].update(
                        unique_physical_object_key=row["acquisition_key"] + "|" + obj["unique_physical_object_key"],
                        geometry_role="visual_box_not_physical_dimensions")
                    accepted += 1
                actions.append({"entity_type": "detection", "entity_id": obj["detection_id"],
                                "correction": correction})
            for obj in item.get("confirmed_additional_vessels", []):
                geometry = obj.get("suggested_visual_geometry") or obj.get("suggested_visual_localization", {}).get("geometry_wgs84")
                validate_geometry(geometry)
                if not area or not shape(area).covers(shape(geometry).centroid):
                    raise ValueError("Omitted positive center is outside reviewed scope")
                actions.append({"entity_type": "addition", "observation_id": oid, "geometry": geometry,
                                "provenance": {**provenance,
                                    "unique_physical_object_key": row["acquisition_key"] + "|" + obj["unique_physical_object_key"],
                                    "geometry_role": "visual_box_not_physical_dimensions", "reason": obj["reason"]}})
                accepted += 1
            correction = {"review_status": "reviewed", "scene_complete": bool(area),
                          "provenance": provenance,
                          "note": "Revisado por IA. " + item["summary"] + " Solo el área explícita contiene etiquetas completas."}
            if area:
                groups = [row["acquisition_key"], pass_id(row["acquisition_key"])]
                if selected["context"]:
                    groups.extend([selected["context"], pass_id(selected["context"])])
                correction.update(reviewed_area=area, dataset_split=selected["split"], split_groups=sorted(set(groups)))
            actions.append({"entity_type": "observation", "entity_id": oid, "correction": correction})
            observations.append({"observation_id": oid, "zone_id": row["zone_id"],
                                 "sensor": row["sensor"], "acquisition_key": row["acquisition_key"],
                                 "split": selected["split"] if area else None, "accepted_zone_labels": accepted})
    plan = {"batch": batch, "author": author, "created_at": utc_text(), "kind": "ai_visual_review",
            "sources": sources, "evidence": evidence, "observations": observations, "actions": actions}
    write_new(destination, plan)
    return plan


def existing_addition(store, action, batch):
    """Resolve an audited omission without writing or hiding a conflicting review."""
    with closing(store.connect()) as tx:
        existing = []
        for row in tx.execute("SELECT detection_id,geometry_json,data_json FROM detections WHERE observation_id=?", (action["observation_id"],)):
            data = json.loads(row["data_json"]).get("provenance", {})
            if data.get("batch") == batch and data.get("unique_physical_object_key") == action["provenance"]["unique_physical_object_key"]:
                if data != action["provenance"] or json.loads(row["geometry_json"]) != action["geometry"]:
                    raise ValueError("An existing omitted object differs from this adjudication")
                existing.append(row["detection_id"])
    if len(existing) > 1:
        raise ValueError("Duplicate omitted object in audited batch")
    return existing[0] if existing else None


def apply(store, plan_path, receipt_path):
    plan = read(plan_path)
    if receipt_path.exists() and read(receipt_path)["plan_sha256"] != sha256_file(plan_path):
        raise ValueError("Receipt belongs to another immutable plan")
    verify_sources(plan)
    for path, digest in plan["evidence"].items():
        if sha256_file(confined_path(ROOT, path)) != digest:
            raise ValueError("Visual evidence checksum changed before applying review")
    author, receipt = plan["author"], []
    # Check every existing entity before mutating any, making interrupted replay safe.
    addition_keys, review_keys = set(), set()
    for action in plan["actions"]:
        if action["entity_type"] == "addition":
            identity = (action["observation_id"], action["provenance"]["unique_physical_object_key"])
            if identity in addition_keys:
                raise ValueError("Duplicate omitted object in adjudication plan")
            addition_keys.add(identity)
            existing_addition(store, action, plan["batch"])
            continue
        identity = (action["entity_type"], action["entity_id"])
        if identity in review_keys:
            raise ValueError("Duplicate review entity in adjudication plan")
        review_keys.add(identity)
        with closing(store.connect()) as tx:
            previous = tx.execute("SELECT correction_json,author FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY rowid DESC LIMIT 1",
                                  (action["entity_type"], action["entity_id"])).fetchone()
        if previous and (json.loads(previous[0]) != action["correction"] or previous[1] != author):
            raise ValueError("A different review already exists; adjudicate the conflict")
    for action in plan["actions"]:
        if action["entity_type"] == "addition":
            existing = existing_addition(store, action, plan["batch"])
            identity = existing or add_detection(store, action["observation_id"], action["geometry"], author, provenance=action["provenance"])
            receipt.append({"entity_type": "addition", "entity_id": identity})
        else:
            with closing(store.connect()) as tx:
                previous = tx.execute("SELECT revision_id,correction_json,author FROM revisions WHERE entity_type=? AND entity_id=? ORDER BY rowid DESC LIMIT 1",
                                      (action["entity_type"], action["entity_id"])).fetchone()
            if previous and (json.loads(previous[1]) != action["correction"] or previous[2] != author):
                raise ValueError("A different review already exists; adjudicate the conflict")
            revision = previous[0] if previous else record_review(store, action["entity_id"], action["correction"], author, entity_type=action["entity_type"])
            receipt.append({"entity_type": action["entity_type"], "entity_id": action["entity_id"], "revision_id": revision})
    result = {"batch": plan["batch"], "plan_sha256": sha256_file(plan_path), "applied_at": utc_text(), "revisions": receipt}
    if receipt_path.exists():
        if read(receipt_path)["plan_sha256"] != result["plan_sha256"]:
            raise ValueError("Receipt belongs to another immutable plan")
    else:
        write_new(receipt_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "apply"))
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--execution-order", type=Path,
                        help="Immutable execution order used to activate reserves (default: original v1)")
    args = parser.parse_args()
    store = Store(ROOT / "data/catalog.sqlite", ROOT / "data")
    if args.action == "prepare":
        if not args.adjudication:
            parser.error("prepare requires --adjudication")
        result = prepare(store, args.adjudication.resolve(), args.plan.resolve(),
                         order_path=args.execution_order.resolve() if args.execution_order else None)
    else:
        if not args.receipt:
            parser.error("apply requires --receipt")
        result = apply(store, args.plan.resolve(), args.receipt.resolve())
    print(json.dumps({"batch": result["batch"], "actions": len(result.get("actions", result.get("revisions", [])))}))


if __name__ == "__main__":
    main()
